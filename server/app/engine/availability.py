"""可用性门控（M3 列表置灰的唯一计算点）。

`MODEL.min_vram_mb` 是**可用性门槛，不是排序依据**（PRD v1.8：引擎不做模型推荐）。

数据来源：引擎阶段 A 的 `DeviceFacts`（T-803 落地）。探测源本身在
`engine/device_probe.py`，本模块只做**门控规则**——规则（控制面）与探测源（数据面）
分离，因此规则可在 T1 上被完整验证。优先复用引擎能力快照里的 facts，
避免每次列表请求都起一次 `nvidia-smi` 子进程。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("websr.engine.availability")


@dataclass(frozen=True)
class HardwareSnapshot:
    """可用性判定所需的硬件事实集（`available_vram_mb` = **实读**可用显存）。"""

    available_vram_mb: int | None  # None = 探测不到（无独显 / 驱动缺失）


_snapshot: HardwareSnapshot | None = None


def probe_hardware_snapshot(*, refresh: bool = False) -> HardwareSnapshot:
    """取硬件快照。优先复用引擎能力快照（阶段 A）的 facts；没有则独立探测一次。"""
    global _snapshot
    if _snapshot is not None and not refresh:
        return _snapshot

    facts = None
    try:
        from . import capabilities as caps_engine  # 局部 import：避免循环依赖

        facts = caps_engine.get_facts()
    except Exception as exc:  # 能力层异常不得影响模型列表可用性
        logger.warning("复用能力快照失败，将独立探测：%s", exc)

    if facts is None:
        try:
            from .device_probe import probe_device_facts

            facts = probe_device_facts()
        except Exception as exc:
            logger.warning("硬件探测失败，按无法判定处理：%s", exc)
            facts = None

    _snapshot = HardwareSnapshot(available_vram_mb=facts.available_vram_mb if facts else None)
    return _snapshot


def reset_snapshot() -> None:
    """清空进程内快照（测试与「重新探测」入口用）。"""
    global _snapshot
    _snapshot = None


def gate_availability(
    *,
    status: str,
    min_vram_mb: int | None,
    snapshot: HardwareSnapshot,
) -> tuple[bool, str | None]:
    """(available, unavailable_reason)。判定顺序固定，文案与前端展示口径一致。"""
    if status == "needs_convert":
        return False, "该模型需先离线转换为 .onnx 后使用"
    if status == "invalid":
        return False, "模型文件校验未通过，请重新导入"
    if min_vram_mb and snapshot.available_vram_mb is not None:
        if min_vram_mb > snapshot.available_vram_mb:
            need = min_vram_mb / 1024
            have = snapshot.available_vram_mb / 1024
            return False, f"需 ≥ {need:.0f} GB 显存 · 当前可用 {have:.1f} GB"
    # 探测不到显存时不门控（宁可可用，也不误灰）——此时档位判定同样按最保守档处理
    return True, None
