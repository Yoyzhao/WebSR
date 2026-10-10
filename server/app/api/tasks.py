"""任务中心路由（api-contract §4.1）。

差异登记（T-700 冻结核对）：`GET /api/tasks` 返回 `Task[]` 数组
（对齐前端已定稿 `fetchTasks(): Task[]`），分页包装待裁决；
SSE 端点 `/api/tasks/{id}/events` 属 T-607。
"""
from fastapi import APIRouter, Query
from sqlalchemy import select

from ..core.errors import AppError
from ..db import get_session
from ..models.entities import Artifact, Task
from ..schemas.task import ArtifactOut, CreateTaskIn, LogEntryOut, TaskOut
from ..services import task_logs
from ..tasks.manager import manager, parse_task_id, serialize_artifact, serialize_task

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _get_task_or_404(s, raw_id: str) -> Task:
    t = s.get(Task, parse_task_id(raw_id))
    if t is None:
        raise AppError("TASK_NOT_FOUND", "任务不存在", "请刷新任务列表后重试", 404)
    return t


@router.post("", status_code=201)
def create_task(body: CreateTaskIn) -> TaskOut:
    task = manager.submit(
        task_type=body.type,
        file_id=body.file_id,
        params=body.params.model_dump(),
    )
    s = get_session()
    try:
        return serialize_task(s.get(Task, task.id), s, manager.runtime_of(task.id))
    finally:
        s.close()


@router.get("")
def list_tasks(
    status: str | None = Query(default=None),
    type: str | None = Query(default=None),
) -> list[TaskOut]:
    s = get_session()
    try:
        stmt = select(Task).order_by(Task.created_at.desc(), Task.id.desc())
        if status:
            stmt = stmt.where(Task.status == status)
        if type:
            stmt = stmt.where(Task.type == type)
        return [serialize_task(t, s, manager.runtime_of(t.id)) for t in s.scalars(stmt).all()]
    finally:
        s.close()


@router.get("/{task_id}")
def get_task(task_id: str) -> TaskOut:
    s = get_session()
    try:
        t = _get_task_or_404(s, task_id)
        return serialize_task(t, s, manager.runtime_of(t.id))
    finally:
        s.close()


@router.post("/{task_id}/cancel")
def cancel_task(task_id: str) -> TaskOut:
    manager.cancel(task_id)
    s = get_session()
    try:
        t = _get_task_or_404(s, task_id)
        return serialize_task(t, s, manager.runtime_of(t.id))
    finally:
        s.close()


@router.get("/{task_id}/artifacts")
def list_artifacts(task_id: str) -> list[ArtifactOut]:
    s = get_session()
    try:
        t = _get_task_or_404(s, task_id)
        rows = s.scalars(select(Artifact).where(Artifact.task_id == t.id)).all()
        execution = (t.resolved or {}).get("execution") or {}
        return [serialize_artifact(a, execution) for a in rows]
    finally:
        s.close()


@router.get("/{task_id}/logs")
def list_logs(task_id: str) -> list[LogEntryOut]:
    """任务日志（api-contract §4.1，T-700 冻结点 #8）。

    返回由任务事实**派生**的结构化条目；级别过滤由前端做（`04` §2.3）。
    """
    s = get_session()
    try:
        t = _get_task_or_404(s, task_id)
        return task_logs.derive_logs(t, task_logs.model_name_for(s, t))
    finally:
        s.close()
