"""SSE 进度推送（api-contract §5，ADR-005 §3）。

事件序列：snapshot（连接即推，断线重连恢复用）→ progress（节流 ≤1s，
由任务管理器广播）→ done（终止，随后服务端主动关闭连接）；间隙以
ping 心跳保活。

⚠️ PING_INTERVAL_SECONDS 是占位值（15s）——契约 §5 明确「间隔与退避策略
由 T-700 定值」。测试可调小。

实现要点：
- 广播队列是同步 `queue.Queue`（工作线程侧发布），消费侧用
  `asyncio.to_thread` 等待，**不阻塞事件循环**；
- 断开（客户端关闭 / done 发出）必须退订，否则订阅队列泄漏。
"""
import asyncio
import json
import queue

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ..core.errors import AppError
from ..db import get_session
from ..models.entities import Task
from ..tasks.broadcaster import broadcaster
from ..tasks.manager import manager, parse_task_id, serialize_task

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

PING_INTERVAL_SECONDS = 15.0  # 占位值，T-700 定稿
_TERMINAL = ("completed", "canceled", "failed", "interrupted")


def _frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/{task_id}/events")
async def task_events(task_id: str) -> StreamingResponse:
    s = get_session()
    try:
        t = s.get(Task, parse_task_id(task_id))
        if t is None:
            raise AppError("TASK_NOT_FOUND", "任务不存在", "请刷新任务列表后重试", 404)
        snapshot = serialize_task(t, s, manager.runtime_of(t.id)).model_dump()
        already_terminal = t.status in _TERMINAL
    finally:
        s.close()

    q = broadcaster.subscribe(task_id)

    async def stream():
        try:
            yield _frame("snapshot", {"task": snapshot})
            if already_terminal:
                # 终态任务的迟到订阅：快照 + 终止帧即关（断线恢复路径）
                yield _frame("done", {"task": snapshot})
                return
            while True:
                try:
                    ev = await asyncio.to_thread(q.get, True, PING_INTERVAL_SECONDS)
                except queue.Empty:
                    yield _frame("ping", {})
                    continue
                if ev.get("type") == "done":
                    yield _frame("done", {"task": ev["task"]})
                    return
                # progress 事件透传契约 §5 字段
                yield _frame("progress", {
                    "task_id": ev["task_id"],
                    "percent": ev["percent"],
                    "current_item": ev["current_item"],
                    "total_items": ev["total_items"],
                    "current_chunk": ev["current_chunk"],
                    "total_chunks": ev["total_chunks"],
                    "stage": ev["stage"],
                    "message": ev["message"],
                })
        finally:
            broadcaster.unsubscribe(task_id, q)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
