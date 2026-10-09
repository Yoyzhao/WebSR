"""保底档与决策参数基线（tech-arch §6.8；决策层的最低层）。

标定（阶段 C，T-805）完成之前，引擎必须有一套**能跑、但处处保守**的参数——这就是保底档。
本模块是这套参数的**单一事实源**：`capabilities.py`（能力面板的保底语义）、
`runtime_profile.py`（阶段 D 决策基线）、`watermark.py`（降档链下界）都从这里取值，
避免"三个地方各写一份 64 / fp32 / 并发 1"。

## 为什么这里可以出现常量

`project-rules.md` §2.2 铁律 1 要求"机制硬编码，数值运行时求"。§6.8 规则 1 给了它**唯一显式例外**：
保底档必须是**下界**，且该下界**不得取开发机实测值**。两个条件缺一不可：

| 取值 | 定位 | 为什么它合规 |
|---|---|---|
| `FALLBACK_MIN_TILE = 64` | **结构下界**：任何 ≥1 GB 可用显存的设备都能安全跑完单块 | 它不是"本机最优"（本机实测 256/512 更快），而是"任何环境都不会 OOM"的下界 |
| `FALLBACK_PRECISION = "fp32"` | CPU 无 fp16 单元时 fp16 反而更慢；GPU 精度性价比属阶段 C 结论 | 保底档**不许**押"这块卡上 fp16 值" |
| `FALLBACK_CONCURRENCY = 1` | 排队并发 1，绝不并行抢资源 | 动态并发上限属 G-06 / T-907 |
| `OVERLAP_RATIO = 1/4` | T-802 质量对照实验正是按 1:4 验证"边界峰比 63.0 → 1.3" | 回归验证过的**比例**，不是实测最优点 |

开发机的实测最优点**永远不会**进这张表——那是阶段 C 标定的产物（且标定属 S2）。

## 两条写进代码的纪律

1. 本模块**不 import** 任何模型层 / 应用层（`models`、`sqlalchemy`、`pydantic`），
   因此"保底档写入 `CalibrationRecord`"或"写进模型元信息"在结构上不可能发生（§6.8 规则 2）；
2. 保底档**不是** `RuntimeProfile.using_fallback` 的判据来源——那个 flag 由阶段 D 决定
   （标定命中即 false）。本模块只提供"标定不可用时的参数基线"。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 保底档取值（§6.8；改动前先读本文件头部的合规条件）
# ---------------------------------------------------------------------------

#: 保底 tile 的**结构下界**（像素）。是"任何环境都不会 OOM"的下界，不是开发机实测最优点。
FALLBACK_MIN_TILE = 64
#: 保底精度：不论 GPU/CPU 一律 fp32（§6.8）。
FALLBACK_PRECISION = "fp32"
#: 保底并发：1（动态并发上限属 G-06 / T-907）。
FALLBACK_CONCURRENCY = 1
#: 后端缺失（EP 验证整体未通过）时的回落标识——**不是**一个 ORT provider 名，
#: 仅用于表达"没有任何已验证后端可用"，界面据此提示"未加速"。
FALLBACK_BACKEND = "cpu"

#: 模型对齐倍数的**保守默认值**：4 / 8 的公倍数。真实取值由 T-806 读 ONNX 输入约束后传入
#: （SwinIR/HAT 类要求窗口倍数，SwinIR 为 8）——本值只是"在拿到约束之前不要算错"的兜底。
DEFAULT_ALIGN = 8

#: 过渡区比例（分子 / 分母）：`overlap = tile / 4`，`feather_px = overlap`（T-802 交接契约推荐值）。
OVERLAP_RATIO_NUM = 1
OVERLAP_RATIO_DEN = 4


# ---------------------------------------------------------------------------
# 对齐工具（tile / overlap 必须落在模型对齐倍数上，否则部分模型直接报尺寸不匹配）
# ---------------------------------------------------------------------------

def align_up(value: int, align: int) -> int:
    """向上取整到 `align` 的倍数（`align <= 1` 时原样返回）。"""
    if align is None or align <= 1:
        return int(value)
    v = int(value)
    return ((v + align - 1) // align) * align


def align_down(value: int, align: int) -> int:
    """向下取整到 `align` 的倍数（`align <= 1` 时原样返回）。"""
    if align is None or align <= 1:
        return int(value)
    v = int(value)
    return (v // align) * align


def overlap_for(tile: int, align: int) -> int:
    """过渡区宽度（= `feather_px`）：`align_down(tile / 4, align)`，并夹在 `(align, tile)` 内。

    夹取是必要的：`overlap >= tile` 会让 `stride = tile - overlap` 退化为 0（T-802 已断言拒绝），
    而 `overlap = 0` 等价于硬切（产品路径禁用）。
    """
    a = max(1, int(align or 1))
    raw = int(tile) * OVERLAP_RATIO_NUM // OVERLAP_RATIO_DEN
    ov = align_down(raw, a)
    if ov < a:
        ov = a
    if ov >= tile:
        ov = max(a, align_down(tile - 1, a))
    return int(ov)


# ---------------------------------------------------------------------------
# 保底档基线
# ---------------------------------------------------------------------------

def fallback_backend(adopted: list[str]) -> tuple[str, str | None]:
    """(`backend`, `reason_or_None`)。取"已验证列表"首项（§6.8），空列表回落 `FALLBACK_BACKEND`。"""
    if adopted:
        return adopted[0], None
    return FALLBACK_BACKEND, "没有任何后端通过 EP 真实性验证，任务将无加速执行"


def fallback_tile(align: int = DEFAULT_ALIGN) -> int:
    """保底 tile：结构下界按模型对齐倍数向上取整（下界本身已经是 8 的倍数，故通常等于下界）。"""
    return align_up(FALLBACK_MIN_TILE, align)


def fallback_params(adopted: list[str], *, align: int = DEFAULT_ALIGN) -> dict:
    """保底档参数基线（阶段 D 的起点，尚未经过因果性防御）。"""
    backend, backend_reason = fallback_backend(adopted)
    tile = fallback_tile(align)
    return {
        "tile": tile,
        "precision": FALLBACK_PRECISION,
        "backend": backend,
        "concurrency": FALLBACK_CONCURRENCY,
        "align": align,
        "backend_reason": backend_reason,
    }


def describe_policy() -> dict:
    """保底档策略摘要（供诊断导出与界面"为什么这么保守"的解释）。"""
    return {
        "min_tile": FALLBACK_MIN_TILE,
        "precision": FALLBACK_PRECISION,
        "concurrency": FALLBACK_CONCURRENCY,
        "overlap_ratio": f"{OVERLAP_RATIO_NUM}/{OVERLAP_RATIO_DEN}",
        "default_align": DEFAULT_ALIGN,
        "note": (
            "保底档 = 标定（阶段 C，S2）完成前的保守下界参数：只允许保守不允许激进，"
            "且不写入标定记录与模型元信息（tech-arch §6.8）"
        ),
    }
