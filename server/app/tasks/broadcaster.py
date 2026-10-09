"""进程内事件广播（ADR-005：SSE 依赖进程内广播 → uvicorn 必须单 worker）。

工作线程（任务执行）发布，SSE 端点（事件循环）订阅；双向都只经过
`queue.Queue`，不加锁竞争之外的共享状态。
"""
from __future__ import annotations

import queue
import threading


class Broadcaster:
    def __init__(self) -> None:
        self._subs: dict[str, list[queue.Queue]] = {}
        self._lock = threading.Lock()

    def subscribe(self, task_id: str) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=256)
        with self._lock:
            self._subs.setdefault(task_id, []).append(q)
        return q

    def unsubscribe(self, task_id: str, q: queue.Queue) -> None:
        with self._lock:
            subs = self._subs.get(task_id)
            if subs and q in subs:
                subs.remove(q)
            if subs == []:
                self._subs.pop(task_id, None)

    def publish(self, task_id: str, event: dict) -> None:
        """扇出到该任务的所有订阅者；队列满则丢弃（订阅方慢不阻塞工作线程，
        进度事件本身允许丢——下一个事件会覆盖最新状态）。"""
        with self._lock:
            targets = list(self._subs.get(task_id, ()))
        for q in targets:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass


broadcaster = Broadcaster()
