"""M1 任务中心管理器：进程内队列 + 工作线程 + 状态机 + 协作式取消 + 广播。

实施约束落点（ADR-005 / tech-arch §6.3）：
- 任务在**独立工作线程**执行，不占事件循环（约束 2）；推理不持有数据库事务
  （约束 1：每次状态翻转都是短事务，执行期间无打开会话）；
- 状态机：queued → running → completed / canceled / failed；canceling 是
  协作式取消中间态；重启后 running/canceling 由启动序列回收为 interrupted（T-603）；
- **并发度 = 1**（保底档保守下界；标定后由引擎反推上限，T-805）：已有任务在
  运行/排队时拒绝新提交（409 TASK_ALREADY_RUNNING），排队策略演进挂账 T-700；
- 进度事件节流 ≤ 1 s（约束 4），落库只记 item 级，chunk 级只走广播；
- 取消不得强杀线程（约束 3）：置标志，执行器在块间自检。
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.errors import AppError
from ..db import get_session
from ..engine.availability import gate_availability, probe_hardware_snapshot
from ..models.entities import Artifact, Model, Task
from ..schemas.task import ArtifactOut, ProgressOut, TaskOut
from ..services import media_store
from ..services.model_registry import get_model_or_404
from .broadcaster import broadcaster
from .executor import TaskContext, TaskCancelled, get_executor

logger = logging.getLogger("websr.tasks.manager")

ID_PREFIX = "tsk_"
TERMINAL_STATUSES = ("completed", "canceled", "failed", "interrupted")
SUPPORTED_TYPES = ("upscale",)  # batch/face/video 为预留类型，随对应功能开放

THROTTLE_SECONDS = 1.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def parse_task_id(raw: str) -> int:
    if not raw.startswith(ID_PREFIX) or not raw[len(ID_PREFIX):].isdigit():
        raise AppError("TASK_NOT_FOUND", "任务不存在", "请刷新任务列表后重试", 404)
    return int(raw[len(ID_PREFIX):])


def serialize_artifact(a: Artifact) -> ArtifactOut:
    return ArtifactOut(
        id=f"art_{a.id}",
        task_id=f"{ID_PREFIX}{a.task_id}",
        kind=a.kind,
        path=a.path,
        filename=Path(a.path).name,
        width=None,
        height=None,
        size_bytes=a.size_bytes,
        sha256=a.sha256,
        created_at=_iso(a.created_at) or "",
    )


def serialize_task(t: Task, s: Session, runtime: dict | None = None) -> TaskOut:
    rt = runtime or {}
    total_items = max(t.progress_total, 1)
    current_chunk = rt.get("current_chunk", 0)
    total_chunks = rt.get("total_chunks", 0)
    if t.status == "completed":
        percent = 1.0
        current_chunk, total_chunks = total_chunks or current_chunk, total_chunks or current_chunk
    elif t.status == "running" and total_chunks:
        # item 级为主、item 内 chunk 折算（单调不减由执行器块序保证）
        percent = min((t.progress_done + current_chunk / total_chunks) / total_items, 1.0)
    else:
        percent = t.progress_done / total_items

    model = s.get(Model, t.model_id) if t.model_id else None
    file_id = (t.params or {}).get("file_id")
    meta = media_store.read_meta(file_id) if file_id else None
    duration_ms = None
    if t.started_at and t.finished_at:
        duration_ms = int((t.finished_at - t.started_at).total_seconds() * 1000)

    artifacts = s.scalars(select(Artifact).where(Artifact.task_id == t.id)).all()

    return TaskOut(
        id=f"{ID_PREFIX}{t.id}",
        type=t.type,
        status=t.status,
        progress=ProgressOut(
            percent=round(percent, 4),
            current_item=t.progress_done,
            total_items=total_items,
            current_chunk=current_chunk,
            total_chunks=total_chunks,
        ),
        params=t.params or {},
        resolved=t.resolved,
        error=t.error,
        model_id=f"mdl_{t.model_id}" if t.model_id else None,
        model_name=model.name if model else None,
        file_id=file_id,
        filename=meta.get("filename") if meta else None,
        source_width=meta.get("width") if meta else None,
        source_height=meta.get("height") if meta else None,
        output_width=None,  # T-808 真实产物回填
        output_height=None,
        duration_ms=duration_ms,
        created_at=_iso(t.created_at) or "",
        started_at=_iso(t.started_at),
        finished_at=_iso(t.finished_at),
        artifacts=[serialize_artifact(a) for a in artifacts],
        ep_evidence=[],  # T-807/T-808 回填
    )


class TaskManager:
    """进程内单例。并发度 1：一个工作线程 + 一条 FIFO 队列。"""

    def __init__(self) -> None:
        self._queue: queue.Queue[int] = queue.Queue()
        self._cancel_flags: dict[int, threading.Event] = {}
        self._runtime: dict[int, dict] = {}
        self._active_id: int | None = None  # 运行中或队列中的任务 id（并发 1）
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- 生命周期（lifespan 调用）----

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker_loop, name="websr-task-worker", daemon=True)
        self._thread.start()
        logger.info("任务工作线程已启动（并发度 1）")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        logger.info("任务工作线程已停止")

    # ---- 提交 ----

    def submit(self, *, task_type: str, file_id: str, params: dict) -> Task:
        if task_type not in SUPPORTED_TYPES:
            raise AppError(
                "VALIDATION_ERROR",
                f"暂不支持的任务类型: {task_type}",
                "当前支持 upscale；批量与视频随对应功能开放",
                400,
            )
        if media_store.read_meta(file_id) is None or media_store.find_upload(file_id) is None:
            raise AppError("NOT_FOUND", "上传的图片不存在或已被清理", "请重新上传后再提交任务", 404)

        s = get_session()
        try:
            model = get_model_or_404(s, params.get("model_id", ""))
            # 可用性门控前置（契约：不该等到提交才失败，但提交仍兜底一次）
            available, reason = gate_availability(
                status=model.status,
                min_vram_mb=model.min_vram_mb,
                snapshot=probe_hardware_snapshot(),
            )
            if not available:
                if model.status == "needs_convert":
                    raise AppError("MODEL_INCOMPATIBLE", reason or "模型需先转换", "请先完成离线转换后再提交", 400)
                raise AppError("MODEL_INSUFFICIENT_VRAM", reason or "显存不足", "请选择可用模型，或降低参数后重试", 409)

            with self._lock:
                if self._active_id is not None:
                    raise AppError(
                        "TASK_ALREADY_RUNNING",
                        "已有任务正在执行",
                        "当前并发度为 1，请等待其完成后再提交",
                        409,
                    )
                task = Task(
                    type=task_type,
                    status="queued",
                    params={**params, "file_id": file_id},
                    model_id=model.id,
                    progress_done=0,
                    progress_total=1,
                )
                s.add(task)
                s.commit()
                s.refresh(task)
                self._active_id = task.id
                self._cancel_flags[task.id] = threading.Event()
                self._queue.put(task.id)
            self._publish_task(task.id, "progress", stage="queued", message="已加入队列")
            return task
        finally:
            s.close()

    # ---- 取消 ----

    def cancel(self, raw_id: str) -> None:
        task_id = parse_task_id(raw_id)
        s = get_session()
        try:
            t = s.get(Task, task_id)
            if t is None:
                raise AppError("TASK_NOT_FOUND", "任务不存在", "请刷新任务列表后重试", 404)
            if t.status in TERMINAL_STATUSES:
                raise AppError("TASK_CANCELED", "任务已结束，无需取消", "请刷新任务列表查看最新状态", 409)
            if t.status == "queued":
                # 尚未被工作线程取走：直接终态化；线程取到时会因状态非 queued 跳过
                t.status = "canceled"
                t.finished_at = _utcnow()
                s.commit()
                self._release_active(task_id)
                self._publish_done(task_id)
            elif t.status == "running":
                # 协作式取消：置中间态 + 标志，执行器块间自检后由工作线程终态化
                t.status = "canceling"
                s.commit()
                flag = self._cancel_flags.get(task_id)
                if flag:
                    flag.set()
                self._publish_task(task_id, "progress", stage=None, message="正在取消")
        finally:
            s.close()

    # ---- 查询辅助 ----

    def runtime_of(self, task_id: int) -> dict:
        return self._runtime.get(task_id, {})

    # ---- 工作线程 ----

    def _release_active(self, task_id: int) -> None:
        with self._lock:
            if self._active_id == task_id:
                self._active_id = None
            self._cancel_flags.pop(task_id, None)

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                task_id = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._execute(task_id)
            except Exception:
                logger.exception("任务执行出现未预期异常: task_id=%s", task_id)
            finally:
                self._release_active(task_id)

    def _execute(self, task_id: int) -> None:
        s = get_session()
        try:
            t = s.get(Task, task_id)
            if t is None or t.status != "queued":
                return  # 排队期间已被取消
            t.status = "running"
            t.started_at = _utcnow()
            s.commit()

            model = s.get(Model, t.model_id)
            file_id = (t.params or {}).get("file_id", "")
            source = media_store.find_upload(file_id)
            flag = self._cancel_flags.setdefault(task_id, threading.Event())
            last_emit = {"ts": 0.0}

            def report(chunk_done: int, chunk_total: int, stage: str, message: str) -> None:
                self._runtime[task_id] = {
                    "current_chunk": chunk_done, "total_chunks": chunk_total,
                    "stage": stage, "message": message,
                }
                now = time.monotonic()
                # 节流 ≤1s（ADR-005 约束 4）；首块与末块必发
                if now - last_emit["ts"] >= THROTTLE_SECONDS or chunk_done in (1, chunk_total):
                    last_emit["ts"] = now
                    self._publish_task(task_id, "progress", stage=stage, message=message)
                    # chunk 级只走广播不落库（tech-arch §4.1）；item 级在状态翻转时落库

            ctx = TaskContext(
                task_id=task_id,
                params=dict(t.params or {}),
                model=model,
                source_path=source,
                should_cancel=flag.is_set,
                report_progress=report,
            )
            try:
                resolved = get_executor().run(ctx)
            except TaskCancelled:
                t = s.get(Task, task_id)
                t.status = "canceled"
                t.finished_at = _utcnow()
                s.commit()
            except Exception as exc:
                logger.exception("任务失败: task_id=%s", task_id)
                t = s.get(Task, task_id)
                t.status = "failed"
                t.error = {
                    "code": "INTERNAL_ERROR",
                    "message": "推理执行失败",
                    "suggestion": "请导出诊断 JSON 并查看日志定位原因",
                    "detail": {"exception": f"{type(exc).__name__}: {exc}"},
                }
                t.finished_at = _utcnow()
                s.commit()
            else:
                t = s.get(Task, task_id)
                t.status = "completed"
                t.resolved = resolved
                t.progress_done = t.progress_total
                t.finished_at = _utcnow()
                s.commit()
            self._publish_done(task_id)
        finally:
            s.close()
            self._runtime.pop(task_id, None)

    # ---- 广播 ----

    def _publish_task(self, task_id: int, event_type: str, stage: str | None, message: str) -> None:
        s = get_session()
        try:
            t = s.get(Task, task_id)
            if t is None:
                return
            out = serialize_task(t, s, self._runtime.get(task_id))
            rt = self._runtime.get(task_id, {})
            broadcaster.publish(f"{ID_PREFIX}{task_id}", {
                "type": "progress" if event_type == "progress" else event_type,
                "task_id": f"{ID_PREFIX}{task_id}",
                "percent": out.progress.percent,
                "current_item": out.progress.current_item,
                "total_items": out.progress.total_items,
                "current_chunk": out.progress.current_chunk,
                "total_chunks": out.progress.total_chunks,
                "stage": stage or rt.get("stage") or ("queued" if t.status == "queued" else "saving"),
                "message": message,
                "status": t.status,
            })
        finally:
            s.close()

    def _publish_done(self, task_id: int) -> None:
        s = get_session()
        try:
            t = s.get(Task, task_id)
            if t is None:
                return
            out = serialize_task(t, s)
            broadcaster.publish(f"{ID_PREFIX}{task_id}", {
                "type": "done",
                "task": out.model_dump(),
            })
        finally:
            s.close()


manager = TaskManager()
