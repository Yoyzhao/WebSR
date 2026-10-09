"""任务执行器协议与占位实现。

M1 只拥有**控制面**（队列 / 状态机 / 取消 / 广播）；真正的推理执行是
M2 引擎的职责，由 **T-808** 以 `EngineExecutor` 替换本文件的 `StubExecutor`。

StubExecutor 的存在理由：没有它，状态机（queued→running→completed /
canceling→canceled / failed）与协作式取消在 5D 阶段没有任何可执行路径
可供验证。它**只做控制面动作**（分块循环、取消检查、进度上报），
不产出任何图像结果与 artifacts——这是诚实的占位，不是假推理。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

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


class Executor(Protocol):
    def run(self, ctx: TaskContext) -> dict:
        """执行并返回 resolved（引擎决策结果）。取消抛 TaskCancelled，失败抛异常。"""
        ...


class StubExecutor:
    """占位执行器（T-808 前）：12 块 × 150ms 模拟推理循环，验证控制面。"""

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
        return {
            "tile": 256,
            "precision": "fp32",
            "backend": ctx.params.get("backend") or "stub",
            "using_fallback": True,
            "degraded": False,
            "reasons": [
                "占位执行器：引擎接入（T-808）前不产生真实推理结果",
                "未标定，取保守下界参数（保底档）",
            ],
            "downgrades": [],
        }


def get_executor() -> Executor:
    """执行器出口（唯一替换点）。T-808 在此返回 EngineExecutor。"""
    return StubExecutor()
