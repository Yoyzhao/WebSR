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
from ..engine import watermark
from ..engine.availability import gate_availability, probe_hardware_snapshot
from ..engine.ep_verify import GPU_PROVIDERS
from ..engine.model_loader import (
    COMPANION_MISSING,
    FILE_MISSING,
    NEEDS_CONVERT,
    RUNTIME_MISSING,
    ModelLoadError,
)
from ..engine.runtime_profile import RuntimeProfile, reprofile
from ..models.entities import Artifact, Model, Task
from ..schemas.task import ArtifactOut, ProgressOut, TaskOut
from ..services import engine_decision, media_store, simulation as simulation_service
from ..services.model_registry import get_model_or_404
from .broadcaster import broadcaster
from .executor import TaskContext, TaskCancelled, get_executor

logger = logging.getLogger("websr.tasks.manager")

ID_PREFIX = "tsk_"
TERMINAL_STATUSES = ("completed", "canceled", "failed", "interrupted")
SUPPORTED_TYPES = ("upscale",)  # batch/face/video 为预留类型，随对应功能开放

#: 契约 §2.3 第 8 条：这四类"制品/后端不兼容"在任务终态统一表达为 `MODEL_INCOMPATIBLE`，
#: 具体原因（含"怎么装"）放 `detail.code` / `suggestion`。
MODEL_INCOMPATIBLE = "MODEL_INCOMPATIBLE"
MODEL_INCOMPATIBLE_CODES = frozenset({
    NEEDS_CONVERT, FILE_MISSING, COMPANION_MISSING, RUNTIME_MISSING,
})

THROTTLE_SECONDS = 1.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe_water_level() -> dict | None:
    """失败现场的水位快照（**永不抛异常**——它只是给错误详情附一份现场）。"""
    try:
        return watermark.sample_water_level().to_dict()
    except Exception:
        return None


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def parse_task_id(raw: str) -> int:
    if not raw.startswith(ID_PREFIX) or not raw[len(ID_PREFIX):].isdigit():
        raise AppError("TASK_NOT_FOUND", "任务不存在", "请刷新任务列表后重试", 404)
    return int(raw[len(ID_PREFIX):])


def serialize_artifact(a: Artifact, execution: dict | None = None) -> ArtifactOut:
    """产物序列化。

    `path` 统一带 `data/` 前缀（与前端已定稿的 Mock 约定一致：
    `data/outputs/tsk_xx/portrait_4x.png`），DB 里存的仍是相对数据根的 `outputs/...`。
    输出产物的宽高取自任务 `resolved.execution`——产物表**不存宽高**（避免为一个派生值
    加列做迁移），真实尺寸在推理结束时已由执行器报出。
    """
    exec_meta = execution or {}
    is_output = a.kind == "output"
    return ArtifactOut(
        id=f"art_{a.id}",
        task_id=f"{ID_PREFIX}{a.task_id}",
        kind=a.kind,
        path=f"data/{a.path}",
        filename=Path(a.path).name,
        width=exec_meta.get("output_width") if is_output else None,
        height=exec_meta.get("output_height") if is_output else None,
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
    # 真实执行事实（T-806）：产物尺寸由执行器写入 resolved.execution，不另读文件
    execution = (t.resolved or {}).get("execution") or {}

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
        output_width=execution.get("output_width"),
        output_height=execution.get("output_height"),
        duration_ms=duration_ms,
        created_at=_iso(t.created_at) or "",
        started_at=_iso(t.started_at),
        finished_at=_iso(t.finished_at),
        artifacts=[serialize_artifact(a, execution) for a in artifacts],
        ep_evidence=[],  # 真实 EP 证据表回填属 T-700 契约定稿后的展示增强
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
                # T-901：模拟态下门控按"声明的可用显存"执行（与列表页口径一致）
                snapshot=probe_hardware_snapshot(simulation=simulation_service.current()),
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

            # ---- 阶段 D：本次任务的运行时决策（在控制面完成，执行器只负责执行）----
            profile = engine_decision.decide_for_task(dict(t.params or {}), model)
            executor = get_executor()

            oom_retry_used = False   # OOM 降档**只重试一次**（§6.1 阶段 E）
            outcome = "failed"
            failure: BaseException | None = None
            resolved: dict | None = None
            run_result = None         # 执行事实（产物清单 / 输出尺寸），T-806 起由执行器写入

            while True:
                ctx = TaskContext(
                    task_id=task_id,
                    params=dict(t.params or {}),
                    model=model,
                    source_path=source,
                    should_cancel=flag.is_set,
                    report_progress=report,
                    profile=profile,
                )
                try:
                    resolved = executor.run(ctx)
                except TaskCancelled:
                    outcome = "canceled"
                except Exception as exc:
                    # ---- 阶段 E：OOM → 降一档 → 重试一次；再失败即降档链耗尽 ----
                    if watermark.is_oom_error(exc) and not oom_retry_used:
                        changes, record = watermark.next_downgrade(
                            profile.params(),
                            adopted=self._adopted_backends(),
                            cause="推理时资源耗尽（OOM）",
                        )
                        if changes:
                            oom_retry_used = True
                            profile = reprofile(profile, changes=changes, record=record)
                            watermark.get_tracker().note_downgrade()
                            logger.warning(
                                "OOM 触发降档重试：%s → %s（task_id=%s）",
                                record["from"], record["to"], task_id,
                            )
                            self._publish_task(
                                task_id, "progress", stage=None,
                                message=f"资源不足，已降档（{record['field']} → {record['to']}）后重试",
                            )
                            continue
                    outcome, failure = "failed", exc
                else:
                    outcome = "completed"
                    run_result = ctx.result
                break

            if outcome == "completed":
                t = s.get(Task, task_id)
                t.status = "completed"
                t.resolved = resolved
                t.progress_done = t.progress_total
                t.finished_at = _utcnow()
                # 真实产物落库（T-806）：磁盘文件已由执行器写好，这里只登记清单与哈希
                for art in (run_result.artifacts if run_result is not None else []):
                    s.add(Artifact(
                        task_id=task_id, kind=art.kind, path=art.path,
                        sha256=art.sha256, size_bytes=art.size_bytes,
                    ))
                s.commit()
            elif outcome == "canceled":
                t = s.get(Task, task_id)
                t.status = "canceled"
                t.finished_at = _utcnow()
                s.commit()
            else:
                self._fail_task(s, task_id, failure, profile)

            # 终态已落库 → **立即释放并发槽**（ADR-005 约束 1：状态翻转即短事务的收尾）。
            # 观测与广播不得占用调度位：水位采样要起一次 nvidia-smi 子进程（百毫秒级），
            # 若拖到持槽位置执行，下一个任务会在"任务已完成"之后仍收到 409
            # TASK_ALREADY_RUNNING——这是 T-804 首次实现时真实踩到的竞态。
            self._release_active(task_id)
            self._publish_done(task_id)
            self._record_water_level(task_id, stage=outcome)
        finally:
            s.close()
            self._runtime.pop(task_id, None)

    # ---- 阶段 D/E 辅助 ----

    def _adopted_backends(self) -> list[str]:
        """当前已验证后端列表（降档链判断"能否切到 CPU"需要）。取不到就当空列表。"""
        try:
            from ..services import system_info

            _, details = system_info.get_capability_snapshot()
            return list(details.get("adopted_backends") or [])
        except Exception as exc:
            logger.debug("读取已验证后端列表失败（降档链按空列表处理）: %s", exc)
            return []

    def _record_water_level(self, task_id: int, *, stage: str) -> None:
        """阶段 E：任务边界采样一次水位并记入历史（采样本身永不抛异常）。

        **边界限制（如实登记）**：任务内的高频采样需要真实推理循环，随 T-806 接入后
        才能"边跑边采"；当前只能采到任务边界，故水位对模拟执行器只证明**机制**成立。
        """
        try:
            level = watermark.sample_water_level()
            entry = watermark.get_tracker().record(level, stage=stage)
            self._runtime.setdefault(task_id, {})["watermark"] = entry
            if entry.get("suggest_downgrade"):
                logger.warning(
                    "任务水位连续 %s 次超阈值（最近 ratio=%s），下一任务将降档",
                    entry.get("consecutive_high"), entry.get("ratio"),
                )
        except Exception as exc:  # 观测失败不得影响任务结果
            logger.debug("水位采样失败（忽略）: %s", exc)

    def _fail_task(
        self, s: Session, task_id: int, exc: BaseException | None, profile: RuntimeProfile
    ) -> None:
        """失败定案。OOM 类失败报显存/内存不足（**降档链耗尽后的终态**），其余报内部错误。"""
        logger.warning("任务失败: task_id=%s exception=%s", task_id, exc)
        t = s.get(Task, task_id)
        if t is None:
            return
        oom = exc is not None and watermark.is_oom_error(exc)
        if oom:
            level = _safe_water_level()
            gpu = profile.backend in GPU_PROVIDERS
            t.error = {
                "code": watermark.insufficient_error_code(profile.backend),
                "message": ("显存不足：已自动降档仍无法完成" if gpu
                            else "物理内存不足：已自动降档仍无法完成"),
                "suggestion": "降低放大倍数、改用更小的模型，或关闭其它占用资源的程序后重试",
                "detail": {
                    "backend": profile.backend,
                    "tile": profile.tile,
                    "precision": profile.precision,
                    "downgrade_attempts": len(profile.downgrades),
                    "water_level": level,
                    "exception": f"{type(exc).__name__}: {exc}",
                },
            }
        else:
            # ---- 模型加载类失败：`ModelLoadError` **自带**机器可判的 `code` 与面向用户的
            #      `reason`（含"怎么装"），**不得**压成通用内部错误。
            #      契约 §2.3 第 8 条明确规定：`needs_convert` / `file_missing` /
            #      `companion_missing` / `runtime_missing` 四类要出现在
            #      `MODEL_INCOMPATIBLE` 的 `detail.code` 里。
            #      （T-703 联调实测：未装 openvino 时，内置 IR 模型只报
            #       "推理执行失败 / 请导出诊断 JSON"，真正的原因与安装指引全被丢掉 ——
            #        这正是「"未安装"是状态不是异常」这条设计被最后一跳抹平的地方。）
            if isinstance(exc, ModelLoadError):
                detail = dict(exc.detail or {})
                detail["code"] = exc.code
                detail.setdefault("exception", f"{type(exc).__name__}: {exc}")
                t.error = {
                    "code": (MODEL_INCOMPATIBLE if exc.code in MODEL_INCOMPATIBLE_CODES
                             else "INTERNAL_ERROR"),
                    "message": exc.message,
                    "suggestion": exc.reason or "请导出诊断 JSON 并查看日志定位原因",
                    "detail": detail,
                }
            else:
                t.error = {
                    "code": "INTERNAL_ERROR",
                    "message": "推理执行失败",
                    "suggestion": "请导出诊断 JSON 并查看日志定位原因",
                    "detail": {"exception": str(exc) if exc is not None else "未知异常"},
                }
        t.status = "failed"
        t.finished_at = _utcnow()
        s.commit()

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
