"""阶段 E：运行时水位反馈与降档（tech-arch §6.1 阶段 E；§6.7 可信显存读取）。

水位回答的是"**这一次跑得多满**"，降档回答的是"**下一次怎么办**"。

| 环节 | 规则 | 出处 |
|---|---|---|
| 采样 | 任务**前**取基线、任务后取峰值；Windows(WDDM) 拿不到按进程显存，只能读设备级 `used` 增量 | P0 报告 §3.3 |
| 判定 | 连续 **2 次** > **85%** → 建议降档；单次高位不触发 | §6.1 阶段 E |
| 降档链 | `tile` 折半（至下界）→ 后端 GPU→CPU（若 CPU 已在 adopted）→ 耗尽 | §2.2 铁律 1 明列"OOM 降档链" |
| OOM | 识别到 OOM 类异常 → 降一档 → **重试一次** | §6.1 阶段 E |
| 口径 | `ratio = max(vram_used/total, ram_used/total)`；显存读不到时只算内存 | **T0 档瓶颈是物理内存不是显存**（§6.2） |

## 三条纪律

1. **采样永不抛异常**：水位是观测，不是判定前置。读不到就记 `None`——
   与阶段 A 同一纪律，绝不让"读不到显存"把任务搞挂。
2. **`fp16` 不在降档链里**：降档只允许"更保守"的动作，而 fp16 在显存上是**变省**的；
   它属于阶段 C 的**精度性价比结论**（"这块卡上 fp16 值不值"），不是压力应对手段。
   把它塞进降档链会得到"OOM 时反而更省显存"的错误语义。
3. **降档必须落进 `resolved.downgrades`**：用户要能在任务详情里看到
   "因为上一次跑满 91%，本次 tile 从 512 降到 256"。

`tools/p0_2_vram_matrix.py` 的显存读取逻辑在此重写为产品代码（§6.7）；
产品代码**不得** import `tools/`。
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field

from .device_probe import read_system_memory_mb
from .ep_verify import GPU_PROVIDERS
from .fallback import (
    DEFAULT_ALIGN,
    FALLBACK_MIN_TILE,
    FALLBACK_PRECISION,
    align_down,
    align_up,
    overlap_for,
)

logger = logging.getLogger("websr.engine.watermark")

#: 单次外部命令超时。水位采样在任务前后各一次，不能成为任务耗时的一部分。
_CMD_TIMEOUT_S = 5

#: 高水位阈值与触发降档所需**连续**次数（§6.1 阶段 E，机制阈值，可硬编码）。
HIGH_WATER_RATIO = 0.85
CONSECUTIVE_HIGH_TO_DOWNGRADE = 2

#: 水位历史保留条数（诊断导出用）
HISTORY_LIMIT = 20


# ---------------------------------------------------------------------------
# 采样
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WaterLevel:
    """一次水位采样。任一字段为 `None` 表示"该项没读到"，**不等于 0**。"""

    vram_used_mb: int | None = None
    vram_total_mb: int | None = None
    ram_used_mb: int | None = None
    ram_total_mb: int | None = None

    @property
    def vram_ratio(self) -> float | None:
        if not self.vram_total_mb or self.vram_used_mb is None:
            return None
        return self.vram_used_mb / self.vram_total_mb

    @property
    def ram_ratio(self) -> float | None:
        if not self.ram_total_mb or self.ram_used_mb is None:
            return None
        return self.ram_used_mb / self.ram_total_mb

    @property
    def ratio(self) -> float | None:
        """压力口径：显存与物理内存**取较大者**（T0 档的瓶颈往往是内存）。"""
        cands = [r for r in (self.vram_ratio, self.ram_ratio) if r is not None]
        return max(cands) if cands else None

    def to_dict(self) -> dict:
        return {
            "vram_used_mb": self.vram_used_mb,
            "vram_total_mb": self.vram_total_mb,
            "ram_used_mb": self.ram_used_mb,
            "ram_total_mb": self.ram_total_mb,
            "vram_ratio": None if self.vram_ratio is None else round(self.vram_ratio, 4),
            "ram_ratio": None if self.ram_ratio is None else round(self.ram_ratio, 4),
            "ratio": None if self.ratio is None else round(self.ratio, 4),
        }


def read_nvidia_memory_mb() -> tuple[int | None, int | None]:
    """(`used_mb`, `total_mb`)，读不到返回 `(None, None)`。**永不抛异常**。

    WDDM 下这是**设备级**用量（含桌面进程占用），不是按进程用量——
    因此它只能用于"水位的相对趋势"，不能当成"本次推理真实占用"。这一限制
    必须原样保留到诊断里，否则会被误读成进程显存。
    """
    try:
        exe = shutil.which("nvidia-smi")
        if not exe:
            return None, None
        out = subprocess.run(
            [exe, "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=_CMD_TIMEOUT_S,
        )
        if out.returncode != 0:
            return None, None
        line = (out.stdout or "").strip().splitlines()
        if not line:
            return None, None
        parts = [p.strip() for p in line[0].split(",")]
        if len(parts) < 2:
            return None, None
        return int(float(parts[0])), int(float(parts[1]))
    except Exception as exc:  # 采样绝不抛异常
        logger.debug("显存水位读取失败（记 None）：%s", exc)
        return None, None


def read_ram_memory_mb() -> tuple[int | None, int | None]:
    """(`used_mb`, `total_mb`)。用 `total - available` 得已用量。读不到返回 `(None, None)`。"""
    total, avail = read_system_memory_mb()
    if total is None or avail is None:
        return None, None
    return max(total - avail, 0), total


def sample_water_level() -> WaterLevel:
    """一次采样。**永不抛异常**（任何失败项记 `None`）。"""
    vram_used, vram_total = read_nvidia_memory_mb()
    ram_used, ram_total = read_ram_memory_mb()
    return WaterLevel(
        vram_used_mb=vram_used, vram_total_mb=vram_total,
        ram_used_mb=ram_used, ram_total_mb=ram_total,
    )


# ---------------------------------------------------------------------------
# 追踪器
# ---------------------------------------------------------------------------

@dataclass
class WaterLevelTracker:
    """进程内水位历史 + "连续高位"计数（阶段 E 的记忆）。

    并发度 1 时无需复杂同步；仍加锁，避免与将来放开并发（G-06）后出现竞态。
    """

    high_ratio: float = HIGH_WATER_RATIO
    consecutive_threshold: int = CONSECUTIVE_HIGH_TO_DOWNGRADE
    history_limit: int = HISTORY_LIMIT
    _history: list[dict] = field(default_factory=list)
    _consecutive_high: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, level: WaterLevel, *, stage: str = "task") -> dict:
        """记录一次水位，返回本次条目（含是否建议降档）。"""
        ratio = level.ratio
        high = ratio is not None and ratio > self.high_ratio
        with self._lock:
            self._consecutive_high = self._consecutive_high + 1 if high else 0
            entry = {
                "stage": stage,
                **level.to_dict(),
                "high": high,
                "consecutive_high": self._consecutive_high,
                "suggest_downgrade": self._consecutive_high >= self.consecutive_threshold,
            }
            self._history.append(entry)
            if len(self._history) > self.history_limit:
                del self._history[: len(self._history) - self.history_limit]
        if entry["suggest_downgrade"]:
            logger.warning(
                "连续 %d 次水位高于 %.0f%%（最近 %.1f%%），建议降档",
                entry["consecutive_high"], self.high_ratio * 100, (ratio or 0) * 100,
            )
        return entry

    def should_downgrade(self) -> bool:
        with self._lock:
            return self._consecutive_high >= self.consecutive_threshold

    def note_downgrade(self) -> None:
        """已执行一次降档 → 计数复位（否则下一任务会因同一批历史重复降档）。"""
        with self._lock:
            self._consecutive_high = 0

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "high_ratio": self.high_ratio,
                "consecutive_threshold": self.consecutive_threshold,
                "consecutive_high": self._consecutive_high,
                "suggest_downgrade": self._consecutive_high >= self.consecutive_threshold,
                "sampling_note": (
                    "Windows(WDDM) 下 vram_* 是设备级用量（含桌面进程），只能看趋势，"
                    "不等于本次推理的真实占用"
                ),
                "history": [dict(h) for h in self._history],
            }

    def reset(self) -> None:
        with self._lock:
            self._history.clear()
            self._consecutive_high = 0


#: 进程内单例（阶段 E 的记忆；并发度 1，无需按任务隔离）
tracker = WaterLevelTracker()


def get_tracker() -> WaterLevelTracker:
    return tracker


def reset_tracker() -> None:
    tracker.reset()


# ---------------------------------------------------------------------------
# OOM 识别与降档链
# ---------------------------------------------------------------------------

_OOM_PATTERNS = (
    "out of memory",
    "outofmemory",
    "out_of_memory",
    "failed to allocate memory",
    "allocate memory",
    "insufficient memory",
    "not enough memory",
    "bad_alloc",
    "内存不足",
    "显存不足",
    "vk_error_out_of_device_memory",
)
#: Windows 提交内存耗尽（页面文件不足）→ RuntimeError 带 WinError 1455
_WIN_OOM_RE = re.compile(r"winerror\s*1455", re.IGNORECASE)


def is_oom_error(exc: BaseException) -> bool:
    """是否为"资源耗尽"类失败。**故意宽松**——多识别一点只会多一次重试，不会误判成功。"""
    text = f"{type(exc).__name__}: {exc}".lower()
    if _WIN_OOM_RE.search(text):
        return True
    return any(pat in text for pat in _OOM_PATTERNS)


def insufficient_error_code(backend: str) -> str:
    """降档链耗尽后应报的错误码：GPU 后端 → 显存；其余 → **物理内存**（T0 的真瓶颈）。"""
    return "VRAM_INSUFFICIENT" if backend in GPU_PROVIDERS else "RAM_INSUFFICIENT"


def next_downgrade(
    params: dict,
    *,
    adopted: list[str],
    align: int = DEFAULT_ALIGN,
    cause: str = "资源水位过高",
) -> tuple[dict | None, dict | None]:
    """降档链的下一个档位。返回 `(changes, record)`；无步可退时返回 `(None, None)`。

    顺序：`tile` 折半（至下界）→ 后端 GPU→CPU（仅当 CPU 已在 adopted）→ 耗尽。
    `precision` 刻意不在链上（见模块头部纪律 2）。
    """
    a = max(1, int(align or DEFAULT_ALIGN))
    tile = int(params.get("tile") or 0)
    backend = str(params.get("backend") or "")

    if tile > FALLBACK_MIN_TILE:
        lower = align_up(max(FALLBACK_MIN_TILE, align_down(tile // 2, a)), a)
        if lower < tile:
            return (
                {"tile": lower, "align": a},
                {
                    "field": "tile", "from": tile, "to": lower,
                    "reason": f"{cause}，块尺寸折半为 {lower}（降低单次推理的峰值占用）",
                },
            )

    if backend in GPU_PROVIDERS and "CPUExecutionProvider" in adopted:
        return (
            {"backend": "CPUExecutionProvider", "align": a},
            {
                "field": "backend", "from": backend, "to": "CPUExecutionProvider",
                "reason": f"{cause}，且 GPU 资源池已不可退让 → 改用 CPU EP（换资源池，不换模型）",
            },
        )

    return None, None


def apply_downgrade_to_params(params: dict, changes: dict, *, align: int = DEFAULT_ALIGN) -> dict:
    """把降档 `changes` 落到参数字典上（重试时用），并同步过渡区宽度。"""
    a = max(1, int(changes.get("align") or align or DEFAULT_ALIGN))
    out = dict(params)
    out.update({k: v for k, v in changes.items() if k != "align"})
    if out.get("tile"):
        out["tile"] = int(out["tile"])
        out["_overlap"] = overlap_for(int(out["tile"]), a)
    out["_align"] = a
    return out


__all__ = [
    "HIGH_WATER_RATIO",
    "CONSECUTIVE_HIGH_TO_DOWNGRADE",
    "WaterLevel",
    "WaterLevelTracker",
    "FALLBACK_MIN_TILE",
    "FALLBACK_PRECISION",
    "apply_downgrade_to_params",
    "get_tracker",
    "insufficient_error_code",
    "is_oom_error",
    "next_downgrade",
    "read_nvidia_memory_mb",
    "read_ram_memory_mb",
    "reset_tracker",
    "sample_water_level",
    "tracker",
]
