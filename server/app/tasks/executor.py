"""任务执行器协议与占位实现。

M1 只拥有**控制面**（队列 / 状态机 / 取消 / 广播）；真正的推理执行是
M2 引擎的职责，由**模型加载器就位后（T-806）**以 `EngineExecutor` 替换本文件的 `StubExecutor`。

**职责分工（T-804 之后明确）**：

- 阶段 D/E 的**决策**在管理器侧完成（`services/engine_decision.py`），随 `TaskContext.profile`
  传入——执行器**不再自己决定** tile / 精度 / 后端，只按决策执行；这样"降了几次、每次为什么"
  能在一个地方（`resolved`）讲清楚；
- 执行器只做**执行与进度上报**，以及块间的取消自检。

StubExecutor 的存在理由：没有它，状态机（queued→running→completed / canceling→canceled /
failed）与协作式取消在 5D 阶段没有任何可执行路径可供验证。它**只做控制面动作**
（分块循环、取消检查、进度上报），**不产出任何图像结果与 artifacts**——
这是诚实的占位，不是假推理：`resolved` 是真实的引擎决策，但**尚未真正驱动计算**。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from ..engine.fallback import DEFAULT_ALIGN, fallback_params, overlap_for
from ..engine.runtime_profile import RuntimeProfile
from ..models.entities import Model


class TaskCancelled(Exception):
    """执行器在每块之间检查取消标志后抛出（协作式取消，ADR-005 约束 3）。"""


@dataclass
class TaskContext:
    task_id: int
    params: dict  # 用户请求值快照（含 file_id）
    model: Model
    source_path: Path
    should_cancel: Callable[[], bool]
    # (chunk_done, chunk_total, stage, message)；节流与落库由管理器负责
    report_progress: Callable[[int, int, str, str], None]
    #: 阶段 D/E 决策结果（管理器注入）。None 时执行器回落到最保守的占位输出。
    profile: RuntimeProfile | None = None


class Executor(Protocol):
    def run(self, ctx: TaskContext) -> dict:
        """执行并返回 resolved（引擎决策结果）。取消抛 TaskCancelled，失败抛异常。"""
        ...


class StubExecutor:
    """占位执行器（T-806 前）：12 块 × 150ms 模拟推理循环，验证控制面。"""

    TOTAL_CHUNKS = 12
    CHUNK_SECONDS = 0.15

    def run(self, ctx: TaskContext) -> dict:
        stages = ["preprocessing", "inferencing", "stitching", "saving"]
        for i in range(1, self.TOTAL_CHUNKS + 1):
            if ctx.should_cancel():
                raise TaskCancelled()
            time.sleep(self.CHUNK_SECONDS)
            stage = stages[min((i - 1) * len(stages) // self.TOTAL_CHUNKS, len(stages) - 1)]
            ctx.report_progress(i, self.TOTAL_CHUNKS, stage, f"正在推理 {i} / {self.TOTAL_CHUNKS} 块")

        if ctx.profile is not None:
            resolved = ctx.profile.to_resolved()
            resolved["reasons"] = list(resolved["reasons"]) + [
                "占位执行器：模型加载器接入（T-806）前不产生真实推理结果——"
                "以上参数是引擎的决策，尚未真正驱动计算"
            ]
            return resolved

        # 无决策注入（独立调用/测试）：给出与保底档同口径的最保守输出。
        # 取值仍走 fallback 单一事实源，避免这里再写一份 64 / fp32 / cpu。
        base = fallback_params([])
        align = DEFAULT_ALIGN
        ov = overlap_for(base["tile"], align)
        return {
            "tile": base["tile"],
            "precision": base["precision"],
            "backend": base["backend"],
            "overlap": ov,
            "feather_px": ov,
            "concurrency": base["concurrency"],
            "using_fallback": True,
            "degraded": False,
            "source": "fallback",
            "reasons": ["未注入引擎决策（占位路径），按最保守参数输出"],
            "downgrades": [],
        }


def get_executor() -> Executor:
    """执行器出口（唯一替换点）。模型加载器就位（T-806）后在此返回 EngineExecutor。"""
    return StubExecutor()
