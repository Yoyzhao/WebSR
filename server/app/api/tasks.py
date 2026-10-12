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
from ..schemas.task import (
    ArtifactOut,
    CreateTaskIn,
    LogEntryOut,
    TaskClearOut,
    TaskDeleteOut,
    TaskOut,
)
from ..services import task_cleanup, task_logs
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
    # 契约 §3.3 冻结点：取消响应体是 `Task`（`status: "canceling"`）。
    # `manager.cancel()` 返回**受理时刻**置下的状态；协作式取消的固有竞态是
    # 工作线程可能在「受理提交」与「下方重读 DB」之间就翻转终态 —— 此时以
    # 受理时刻为准返回 `canceling`（客户端随后 GET 会看到真实终态 `canceled`）。
    status_at_cancel = manager.cancel(task_id)
    s = get_session()
    try:
        t = _get_task_or_404(s, task_id)
        out = serialize_task(t, s, manager.runtime_of(t.id))
        if status_at_cancel == "canceling" and out.status != "canceling":
            out.status = "canceling"
        return out
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


# ---------------------------------------------------------------------------
# 清理（api-contract v1.1 §4.1，CHG-20261011-08）
#
# ⚠️ 批量清理的路由必须**先于**任何单段 POST 路由声明：`/clear-finished` 与
#    `/{task_id}` 同为单段路径，声明晚会被当成 task_id 吃掉。
#    （当前 `POST ""` 是空段、`POST /{task_id}/cancel` 是两段，暂不冲突，
#     但把顺序写成自解释的，避免以后有人加 `/api/tasks/{task_id}` 时踩这个坑。）
# ---------------------------------------------------------------------------


@router.post("/clear-finished")
def clear_finished_tasks() -> TaskClearOut:
    """清理全部终态任务（含其产出文件）。返回按状态分类的真实计数。"""
    s = get_session()
    try:
        return TaskClearOut(**task_cleanup.clear_finished(s))
    finally:
        s.close()


@router.delete("/{task_id}")
def delete_task(task_id: str) -> TaskDeleteOut:
    """删除单个终态任务（含其产出文件）。

    非终态 → `409 TASK_NOT_TERMINAL`（先取消）；不存在 → `TASK_NOT_FOUND`。
    """
    s = get_session()
    try:
        return TaskDeleteOut(**task_cleanup.delete_task(s, task_id))
    finally:
        s.close()
