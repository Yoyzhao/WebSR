"""阶段 D：运行时决策（tech-arch §6.1 阶段 D；契约 `TaskResolved`）。

回答一个问题：**这次任务用多大的块、什么精度、哪个后端**——并且把"为什么这么定"
逐条写下来（`reasons`），把"哪一项被降了"逐条写下来（`downgrades`）。

## 决策顺序（`decide_profile`）

```
① 基线   标定有效且指纹一致 → 标定结论（source=calibration，using_fallback=False）
          否则              → 保底档（source=fallback，using_fallback=True，§6.8）
② 用户覆盖  auto=False 且字段非空 → 覆盖对应项（source=user）
③ 因果性防御（**与来源无关，一律执行**，每命中一条记一条 downgrade）
          backend ∉ adopted                     → 回落 adopted[0]
          precision == fp16 且后端不是 GPU       → 降 fp32（CPU 路径铁律）
          precision == fp16 且 GPU 无 fp16 单元  → 降 fp32（按 compute_cap，不按型号）
          tile 小于下界 / 不是 align 倍数         → 抬升并向上取整
④ 过渡区  overlap = align_down(tile/4, align)，feather_px = overlap
⑤ 兜底    adopted 为空 → 强制 using_fallback=True（无加速可用，不冒领）
⑥ degraded = downgrades 非空
```

## 为什么防御"不信任来源"

标定结论也会失效：换卡后指纹不符、标定在 CPU 档做的而现在插上了 GPU、标定脚本本身有 bug。
用户手填的参数更容易自相矛盾（CPU + fp16）。所以防御必须是**因果性**的——
它对三类来源（标定 / 用户 / 保底）一视同仁，触发就把 `degraded` 置真并记录原因，
界面"本次决策"据此展示，对应 PRD"降级必须显式"。

## 纯度

本模块只依赖 `fallback` / `device_probe` / `ep_verify`（全部同为引擎层、无应用层依赖），
不 import fastapi / sqlalchemy / pydantic；读标定与建快照由应用层组装件
（`services/engine_decision.py`）负责。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from .device_probe import DeviceFacts
from .ep_verify import GPU_PROVIDERS
from .fallback import (
    DEFAULT_ALIGN,
    FALLBACK_MIN_TILE,
    FALLBACK_PRECISION,
    align_up,
    fallback_params,
    overlap_for,
)

#: 合法精度取值（其余一律按 fp32 处理并记一条降档——不接受未知字符串静默透传）
VALID_PRECISIONS = ("fp32", "fp16")


# ---------------------------------------------------------------------------
# 输入
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CalibrationView:
    """阶段 C（T-805）标定结论的**消费视图**。

    T-804 不产生标定记录，只定义"拿到记录后怎么用"。字段取自有记录时的最小充分集：

    - `tile`：来自 `Calibration.tile_curve` 的推荐块尺寸（曲线形状由 T-805 定义）；
    - `precision`：来自 `Calibration.precision_decision`；
    - `hardware_fingerprint`：**失效判据**（指纹不符即整份作废，与 EP 缓存同一纪律）；
    - `valid`：记录自身是否仍然有效。
    """

    hardware_fingerprint: str | None = None
    tile: int | None = None
    precision: str | None = None
    backend: str | None = None
    concurrency: int | None = None
    reason: str | None = None
    valid: bool = True

    def usable_with(self, fingerprint: str | None) -> bool:
        """记录有效**且**指纹一致才算可用（指纹缺失时保守判为不可用）。"""
        if not self.valid:
            return False
        if not self.hardware_fingerprint or not fingerprint:
            return False
        return self.hardware_fingerprint == fingerprint


@dataclass(frozen=True)
class RuntimeRequest:
    """用户请求值（`TASK.params`）。`auto=True` 时三个字段应为空。"""

    auto: bool = True
    tile: int | None = None
    precision: str | None = None
    backend: str | None = None
    scale: int | None = None

    @classmethod
    def from_params(cls, params: dict | None) -> "RuntimeRequest":
        p = params or {}
        auto = p.get("auto")
        return cls(
            auto=True if auto is None else bool(auto),
            tile=_as_int(p.get("tile")),
            precision=(p.get("precision") or None),
            backend=(p.get("backend") or None),
            scale=_as_int(p.get("scale")),
        )


def _as_int(v) -> int | None:
    try:
        return int(v) if v is not None and str(v).strip() != "" else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------

@dataclass
class RuntimeProfile:
    """阶段 D 产物。`to_resolved()` 给出契约 `TaskResolved` 形状。"""

    tile: int
    precision: str
    backend: str
    overlap: int
    feather_px: int
    concurrency: int
    using_fallback: bool
    source: str  # calibration | user | fallback
    reasons: list[str] = field(default_factory=list)
    downgrades: list[dict] = field(default_factory=list)

    @property
    def degraded(self) -> bool:
        return bool(self.downgrades)

    def to_resolved(self) -> dict:
        """契约字段在前；`overlap` / `feather_px` / `concurrency` / `source` 为新增项（挂账 T-700）。"""
        return {
            "tile": self.tile,
            "precision": self.precision,
            "backend": self.backend,
            "using_fallback": self.using_fallback,
            "degraded": self.degraded,
            "reasons": list(self.reasons),
            "downgrades": list(self.downgrades),
            # ↓ M2 编排（T-806/T-808）驱动 M4 分块需要 overlap/feather；source 供"本次决策"展示
            "overlap": self.overlap,
            "feather_px": self.feather_px,
            "concurrency": self.concurrency,
            "source": self.source,
        }

    def params(self) -> dict:
        """降档链的输入形态（只含可降的三项 + 过渡区）。"""
        return {"tile": self.tile, "precision": self.precision, "backend": self.backend}


def _downgrade(field_name: str, old, new, reason: str) -> dict:
    return {"field": field_name, "from": old, "to": new, "reason": reason}


# ---------------------------------------------------------------------------
# 阶段 D 主入口
# ---------------------------------------------------------------------------

def decide_profile(
    *,
    facts: DeviceFacts | None,
    adopted: list[str],
    fingerprint: str | None = None,
    calibration: CalibrationView | None = None,
    request: RuntimeRequest | None = None,
    align: int = DEFAULT_ALIGN,
    fixed_tile: int | None = None,
) -> RuntimeProfile:
    """执行完整决策。**永不抛异常**——任何异常输入都退化为保底档（可用性优先）。

    Args:
        align: **分块边长的倍数约束**（SwinIR 类窗口倍数）。由 T-806 读模型输入约束后传入；
            读不到硬约束时用 `DEFAULT_ALIGN` 的保守下界（读不出 ≠ 无约束，见
            `model_introspect`）。
        fixed_tile: **模型输入为静态尺寸**时，tile 必须**正好等于**该值（T-806 读 ONNX 发现）。
            它**不能**用 `align` 表达：`align = 512` 会让 `overlap_for(512, 512)` 算出
            `overlap == tile`，使 `stride = 0`，`plan_tiles` 直接报错。两者语义不同，故分开。
    """
    req = request or RuntimeRequest()
    reasons: list[str] = []
    downgrades: list[dict] = []

    try:
        a = max(1, int(align or DEFAULT_ALIGN))
    except (TypeError, ValueError):
        a = DEFAULT_ALIGN

    # ---- ① 基线 -----------------------------------------------------------------
    cal_ok = calibration is not None and calibration.usable_with(fingerprint)
    if cal_ok:
        base_tile = calibration.tile or 0
        base_precision = calibration.precision or FALLBACK_PRECISION
        base_backend = calibration.backend or ""
        base_concurrency = calibration.concurrency or 1
        source = "calibration"
        using_fallback = False
        reasons.append(
            f"标定记录命中（硬件指纹一致）：{calibration.reason or '采用标定结论'}"
        )
    else:
        base = fallback_params(adopted, align=a)
        base_tile = base["tile"]
        base_precision = base["precision"]
        base_backend = base["backend"]
        base_concurrency = base["concurrency"]
        source = "fallback"
        using_fallback = True
        if calibration is not None and not cal_ok:
            reasons.append("存在标定记录但与当前硬件指纹不符（或已失效）→ 回落保底档")
        elif calibration is not None:
            reasons.append("标定记录不可用 → 回落保底档")
        else:
            reasons.append("未标定（阶段 C 属 S2），采用保底档保守下界参数")
        if base.get("backend_reason"):
            reasons.append(base["backend_reason"])

    # ---- ② 用户覆盖 ---------------------------------------------------------------
    if not req.auto:
        source = "user"
        overrode: list[str] = []
        if req.tile:
            base_tile = req.tile
            overrode.append("tile")
        if req.precision:
            base_precision = req.precision
            overrode.append("precision")
        if req.backend:
            base_backend = req.backend
            overrode.append("backend")
        if overrode:
            reasons.append(f"用户指定参数（非自动档）：{'、'.join(overrode)}")
        else:
            reasons.append("用户关闭了自动档但未指定具体参数 → 仍按基线取值")
    else:
        reasons.append("自动档：tile / 精度 / 后端由引擎决定（模型始终由用户选择）")

    # ---- ③ 因果性防御（与来源无关） -------------------------------------------------
    precision = base_precision if base_precision in VALID_PRECISIONS else FALLBACK_PRECISION
    if base_precision not in VALID_PRECISIONS:
        downgrades.append(_downgrade(
            "precision", base_precision, FALLBACK_PRECISION,
            f"未知精度取值 {base_precision!r}，按最保守的 fp32 处理",
        ))
        precision = FALLBACK_PRECISION

    backend = base_backend or (adopted[0] if adopted else "")
    if backend not in adopted:
        new_backend = adopted[0] if adopted else ""
        downgrades.append(_downgrade(
            "backend", backend or "(未指定)", new_backend or "(无已验证后端)",
            "该后端未通过 EP 真实性验证（节点级归属）——静默使用会得到「看似加速实则更慢」的结果",
        ))
        backend = new_backend

    is_gpu = backend in GPU_PROVIDERS
    if precision == "fp16" and not is_gpu:
        downgrades.append(_downgrade(
            "precision", "fp16", "fp32",
            "CPU 路径禁止 fp16（无 fp16 加速单元的桌面 CPU 上 fp16 反而更慢）",
        ))
        precision = "fp32"
    elif precision == "fp16":
        supports = facts.primary_nvidia.supports_fp16 if (facts and facts.primary_nvidia) else None
        if supports is not True:
            why = ("无法确认该卡具备 fp16 单元（compute_cap 未知）"
                   if supports is None else "该卡 compute_cap < 5.3，不具备 fp16 单元")
            downgrades.append(_downgrade("precision", "fp16", "fp32", f"{why}，改用 fp32"))
            precision = "fp32"

    if fixed_tile is not None and _as_int(fixed_tile) and int(fixed_tile) > 0:
        # 模型输入为静态尺寸：tile 必须**正好等于**它（多一分少一分 ORT 都报尺寸不匹配）
        tile = int(fixed_tile)
        if tile != (_as_int(base_tile) or 0):
            reasons.append(f"模型输入为固定 {tile}×{tile}，分块边长据其确定（读自 ONNX 输入约束）")
    else:
        tile = align_up(max(_as_int(base_tile) or 0, FALLBACK_MIN_TILE), a)
        if tile != (base_tile or 0):
            downgrades.append(_downgrade(
                "tile", base_tile or 0, tile,
                f"块尺寸必须不小于下界 {FALLBACK_MIN_TILE} 且为模型对齐倍数 {a} 的整数倍",
            ))

    # ---- ④ 过渡区 -----------------------------------------------------------------
    overlap = overlap_for(tile, a)

    # ---- ⑤ 兜底 -------------------------------------------------------------------
    if not adopted:
        # 没有可信后端：**未标定时**直接判为保底档（§6.8）。
        # 但若标定记录有效，则"档位"由标定裁决（§6.8 表格第 2 行），
        # 此时 using_fallback 保持 false——缺后端这件事已由 ③ 的 backend 回落
        # 与 degraded=true 如实表达，不必重复改写档位语义。
        if not cal_ok:
            using_fallback = True
        if not any("EP 真实性验证" in r for r in reasons):
            reasons.append("没有任何后端通过 EP 真实性验证 → 任务不获得加速")

    # ---- ⑥ 收尾 -------------------------------------------------------------------
    concurrency = 1 if using_fallback else max(1, _as_int(base_concurrency) or 1)
    if downgrades:
        reasons.extend(d["reason"] for d in downgrades)

    return RuntimeProfile(
        tile=int(tile),
        precision=precision,
        backend=backend,
        overlap=int(overlap),
        feather_px=int(overlap),
        concurrency=concurrency,
        using_fallback=using_fallback,
        source=source,
        reasons=reasons,
        downgrades=downgrades,
    )


def reprofile(
    profile: RuntimeProfile,
    *,
    changes: dict,
    record: dict | None = None,
    reasons: list[str] | None = None,
) -> RuntimeProfile:
    """在既有决策上叠加一次降档（阶段 E 的 OOM / 水位降档路径）。

    **不重新决策**——只把已经决定要降的那一项换掉，并把原因追加进 `downgrades`，
    这样"降了几次、每次为什么"在任务详情里完整可查。
    """
    new = replace(
        profile,
        reasons=list(profile.reasons),
        downgrades=list(profile.downgrades),
    )
    if "tile" in changes:
        new.tile = int(changes["tile"])
    if "precision" in changes:
        new.precision = str(changes["precision"])
    if "backend" in changes:
        new.backend = str(changes["backend"])
    align = int(changes.get("align") or DEFAULT_ALIGN)
    new.overlap = overlap_for(new.tile, align)
    new.feather_px = new.overlap
    if record:
        new.downgrades.append(record)
        new.reasons.append(record["reason"])
    if reasons:
        new.reasons.extend(reasons)
    return new
