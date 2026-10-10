"""档位模拟的**应用层取数**（T-901）。

引擎层（`engine/simulation.py`）只认一个纯数据结构 `SimulationOverride`，
"这四个值从哪读、什么时候作废缓存"是应用层的问题——放在这里，引擎层保持不读库。

## 缓存与作废

设置覆盖行在 `settings_store` 里有进程内缓存；能力快照 / 硬件快照也有各自的缓存。
**切换模拟开关必须让这三者同时作废**，否则会出现"设置改了、能力面板不变"的假象。
本模块提供 `current()`（读）与 `invalidate()`（作废下游缓存），
由 `settings_store.update_settings()` 在保存后调用 `invalidate()`。
"""

from __future__ import annotations

import logging

from ..engine import simulation as engine_simulation
from ..engine.simulation import SimulationOverride

logger = logging.getLogger("websr.services.simulation")

#: 设置表里的键名（与 `settings_store._SPECS` 同源，改一处必须同步）
KEY_ENABLED = "simulation_enabled"
KEY_FORCE_TIER = "force_tier"
KEY_FORCE_VRAM_MB = "force_vram_mb"
KEY_FORCE_TENSORRT = "force_has_tensorrt"

#: 本模块负责的键（`settings_store` 用它判断"是否需要作废下游缓存"）
SIMULATION_KEYS = (KEY_ENABLED, KEY_FORCE_TIER, KEY_FORCE_VRAM_MB, KEY_FORCE_TENSORRT)


def current() -> SimulationOverride:
    """当前生效的模拟声明。**永不抛异常**：读不到设置就当作未开启。

    设置未就绪（启动早期 / 库不可用）时返回 `disabled()`——模拟是开发期能力，
    绝不能因为读不到它的配置而影响 T1 主链路（PRD §7.1 硬要求 3）。
    """
    try:
        from . import settings_store  # 局部 import：避免模块级环

        return engine_simulation.from_values(
            enabled=settings_store.effective(KEY_ENABLED),
            force_tier=settings_store.effective(KEY_FORCE_TIER),
            force_vram_mb=settings_store.effective(KEY_FORCE_VRAM_MB),
            force_has_tensorrt=settings_store.effective(KEY_FORCE_TENSORRT),
        )
    except Exception as exc:  # 读不到 → 未开启（保守方向：一切按真实硬件走）
        logger.debug("档位模拟设置读取失败，按未开启处理: %s", exc)
        return engine_simulation.disabled()


def invalidate() -> None:
    """作废所有"读到了模拟值"的下游缓存。**永不抛异常**。

    必须三处都清：
    - 设置覆盖行（`settings_store` 自己会在保存后清）；
    - 能力快照（含 `tier` / `simulation` 块）；
    - 硬件快照（含参与模型门控的可用显存）。
    """
    try:
        from ..engine import availability, capabilities

        capabilities.reset_snapshot()
        availability.reset_snapshot()
    except Exception as exc:
        logger.warning("模拟态切换后作废快照失败（下次重建将自愈）: %s", exc)
