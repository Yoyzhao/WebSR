"""阶段 D/E 的应用层组装件（tech-arch §6.1 D/E；T-804）。

引擎层（`engine/runtime_profile.py` / `watermark.py`）是**纯函数**——不读库、不读配置。
"标定记录从哪来、能力快照取自哪个数据根、水位历史存在哪"这类问题留在这里回答，
所以两个模块的判据都能在任意环境里脱离应用栈**单独复核**（引擎纯度，脚本会做源码级检查）。

职责只有三件：

1. 组装决策输入：能力快照（阶段 A/B）→ `facts` / `adopted` / `fingerprint`；
   标定记录（阶段 C）→ `CalibrationView`；
2. 调用 `decide_profile()` 得到阶段 D 结果，并叠加阶段 E 的"连续高位 → 降一档"；
3. 记住"最近一次决策"，供诊断导出解释"为什么他慢"。

**不写任何持久化**：决策结果只随任务快照进 `TASK.resolved`；保底档**不写**标定表与
模型元信息（§6.8 规则 2），本模块也不产生任何 `Calibration` 行。
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from ..engine import watermark
from ..engine.capabilities import get_facts
from ..engine.fallback import DEFAULT_ALIGN, describe_policy, fallback_params, overlap_for
from ..engine.runtime_profile import (
    RuntimeProfile,
    RuntimeRequest,
    decide_profile,
    reprofile,
)
from ..models.entities import Model
from . import system_info

logger = logging.getLogger("websr.services.engine_decision")

_lock = threading.Lock()
_last: dict | None = None


# ---------------------------------------------------------------------------
# 阶段 D 决策
# ---------------------------------------------------------------------------

def model_constraints(model: Model | None) -> tuple[int, int | None, str | None]:
    """读模型真实的输入约束 → `(align, fixed_tile, 说明)`（**T-806 兑现 T-804 的挂账**）。

    取值口径（**只收紧、不放宽**）：读到静态硬约束就服从它，否则保留 `DEFAULT_ALIGN`
    的保守下界。`model_introspect` 在动态输入下给出的 `align=1` 含义是"**未检测到**约束"，
    不是"无约束"——直接采用会把一个本来安全的 tile 变成非法尺寸（窗口注意力模型同样
    导出为动态 H/W）。

    `fixed_tile` 与 `align` **必须分开**：`align` 是"tile 需为其整数倍"（还参与 overlap
    对齐），而静态输入要求的是"tile 必须正好等于它"。把后者塞进 `align` 会让
    `overlap_for(512, 512)` 得出 `overlap == tile`，`stride` 退化为 0。

    **永不抛异常**：任何失败都回落保守默认值（读约束不该成为任务失败的原因）。
    """
    default: tuple[int, int | None, str | None] = (DEFAULT_ALIGN, None, None)
    if model is None or model.format != "onnx":
        return default
    try:
        from ..engine import model_introspect  # 局部 import：仅 onnx 路径需要
        from . import model_registry

        spec = model_introspect.spec_for_path(model_registry.models_dir() / model.path)
    except Exception as exc:
        logger.debug("读取模型输入约束失败，按保守默认对齐处理：%s", exc)
        return default

    if spec.fixed_tile:
        n = int(spec.fixed_tile)
        return DEFAULT_ALIGN, n, f"模型输入为固定 {n}×{n}，分块边长据其确定（读自 ONNX 输入约束）"
    if spec.align and spec.align > DEFAULT_ALIGN:
        return int(spec.align), None, f"模型输入对齐倍数为 {spec.align}（读自 ONNX 输入约束）"
    return default


def decide_for_task(params: dict | None, model: Model | None) -> RuntimeProfile:
    """为一个任务做决策（阶段 D + 阶段 E 的降档叠加）。**永不抛异常**。

    标定记录按 `(模型, 硬件指纹)` 精确取；能力快照走 `system_info` 的同一份进程内缓存，
    **不重跑**阶段 A/B。标定取数会比快照多读一次库（快照用的是"通用记录"口径），
    但表在 S2 前恒为空，代价是常数级。
    """
    try:
        caps, details = system_info.get_capability_snapshot()
        facts = get_facts()
        fingerprint = details.get("hardware_fingerprint")
        adopted = list(details.get("adopted_backends") or [])
        cal = system_info.read_calibration_view(fingerprint, model.id if model else None)
        align, fixed_tile, constraint_note = model_constraints(model)
        profile = decide_profile(
            facts=facts,
            adopted=adopted,
            fingerprint=fingerprint,
            calibration=cal,
            request=RuntimeRequest.from_params(params),
            align=align,  # T-806：由 ONNX 输入约束求真实值，取不到则保守默认
            fixed_tile=fixed_tile,
        )
        if constraint_note and constraint_note not in profile.reasons:
            profile.reasons.append(constraint_note)
        profile = _apply_water_level_downgrade(profile, adopted=adopted, align=align)
        _remember(profile, caps.get("tier"))
        return profile
    except Exception as exc:
        # 决策自身失效也必须给得出可用结果：退化为最保守的"纯 CPU + 最小块 + fp32"。
        # 取值仍来自 fallback 单一事实源（不在这里另写一份 64/16/cpu）。
        logger.exception("阶段 D 决策异常，退化为最保守参数: %s", exc)
        base = fallback_params([])
        tile = base["tile"]
        ov = overlap_for(tile, DEFAULT_ALIGN)
        degraded = RuntimeProfile(
            tile=tile, precision=base["precision"], backend=base["backend"],
            overlap=ov, feather_px=ov, concurrency=base["concurrency"],
            using_fallback=True, source="fallback",
            reasons=[f"决策过程异常（{type(exc).__name__}），退化为最保守参数以保可用"],
        )
        _remember(degraded, None)
        return degraded


def _apply_water_level_downgrade(
    profile: RuntimeProfile, *, adopted: list[str], align: int = DEFAULT_ALIGN
) -> RuntimeProfile:
    """阶段 E：**连续高位**时在本次决策上先降一档（§6.1：连续 2 次 > 85% 降档）。"""
    tr = watermark.get_tracker()
    if not tr.should_downgrade():
        return profile
    cause = (
        f"连续 {tr.consecutive_threshold} 次资源水位超过 {int(tr.high_ratio * 100)}%"
        "（阶段 E 反馈）"
    )
    changes, record = watermark.next_downgrade(
        profile.params(), adopted=adopted, align=align, cause=cause
    )
    if not changes:
        profile.reasons.append(f"{cause}，但已到下界（无可再降的档位）")
        tr.note_downgrade()  # 已到下界，避免每个任务都重复报警
        return profile
    tr.note_downgrade()
    downgraded = reprofile(profile, changes=changes, record=record)
    logger.warning("阶段 E 触发降档：%s → %s（%s）", record["from"], record["to"], record["reason"])
    return downgraded


def _remember(profile: RuntimeProfile, tier: str | None) -> None:
    global _last
    with _lock:
        _last = {
            "decided_at": datetime.now(timezone.utc).isoformat(),
            "tier": tier,
            "resolved": profile.to_resolved(),
        }


def last_decision() -> dict | None:
    """最近一次决策（诊断导出用）。无决策时返回 None。"""
    with _lock:
        return dict(_last) if _last else None


def reset_last_decision() -> None:
    global _last
    with _lock:
        _last = None


def decision_policy() -> dict:
    """决策策略摘要（诊断导出用）：保底档取值 + 水位阈值 + 降档链顺序。"""
    return {
        "fallback": describe_policy(),
        "watermark": {
            "high_ratio": watermark.HIGH_WATER_RATIO,
            "consecutive_high_to_downgrade": watermark.CONSECUTIVE_HIGH_TO_DOWNGRADE,
            "downgrade_chain": ["tile 折半（至下界）", "后端 GPU→CPU", "耗尽即报错"],
            "oom_retry_once": True,
            "note": "fp16 不在降档链（降档只做更保守的动作；精度性价比属阶段 C 标定）",
        },
        "sources": ["calibration（标定命中）", "user（非自动档指定）", "fallback（保底档）"],
    }


def watermark_snapshot() -> dict:
    """水位历史快照（诊断导出用）。"""
    return watermark.get_tracker().snapshot()
