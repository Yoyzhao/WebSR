"""任务日志派生（api-contract §4.1 `GET /api/tasks/{id}/logs`，T-700）。

返回**由任务事实派生的结构化条目**（时间线 + 降档 + 错误），**不是**原始进程 stdout
抓取——原始日志仍在服务端日志文件里，这里是"给用户看的排障摘要"。

派生规则（契约 §4.1，冻结点 #8）：

- `info`    ：创建 / 开始 / 完成（含模型、后端、耗时、产物）
- `warning` ：每条 `resolved.downgrades[]`（降档必须显式）、保底档运行、进程中断
- `error`   ：失败任务的 `error.code` + `error.message`，条目带 `code` 字段
- **无 warning / error 的正常任务返回一条 `info`**（"本次任务无警告或错误记录"），
  **不返回空数组**——避免前端把"空"误读为"加载失败"。

级别过滤由**前端**做（`04` §2.3：默认只显示 `warning` 以上），服务端**不做** `?level=` 过滤。
"""
from __future__ import annotations

from datetime import datetime

from ..models.entities import Model, Task

_LEVEL_ORDER = {"debug": 0, "info": 1, "warning": 2, "error": 3}


def _iso(dt: datetime | None) -> str | None:
    """UTC datetime → ISO 8601 带偏移（与 `tasks/manager.py::_iso` **逐字同口径**）。

    刻意**本地复制**而非 import：`tasks/manager` 反向依赖 `services/*`，
    在此处 import 会形成模块环。
    """
    return dt.isoformat() if dt else None


def _entry(level: str, timestamp: str | None, message: str, code: str | None = None) -> dict:
    item: dict = {"level": level, "timestamp": timestamp or "", "message": message}
    if code:
        item["code"] = code
    return item


def derive_logs(task: Task, model_name: str | None = None) -> list[dict]:
    """把一条任务派生为有序的日志条目（时间升序）。"""
    resolved = task.resolved or {}
    execution = resolved.get("execution") or {}
    entries: list[dict] = []

    created = _iso(task.created_at)
    started = _iso(task.started_at)
    finished = _iso(task.finished_at)

    label = model_name or (task.params or {}).get("model_id") or "(未指定模型)"

    # ---- 创建 ----------------------------------------------------------------
    entries.append(_entry("info", created, f"任务创建 · 模型 {label} · 类型 {task.type}"))

    # ---- 开始 ----------------------------------------------------------------
    if task.started_at is not None:
        backend = resolved.get("backend") or "未确定"
        tile = resolved.get("tile")
        precision = resolved.get("precision")
        detail = f"，tile {tile}，精度 {precision}" if tile else ""
        entries.append(_entry("info", started, f"任务开始 · 后端 {backend}{detail}"))

    # ---- 决策说明（保底档 / 降档）--------------------------------------------
    if resolved.get("using_fallback"):
        entries.append(_entry(
            "warning", started or created,
            "处于保底档：标定未命中，采用保守下界参数",
        ))
    for d in resolved.get("downgrades") or []:
        entries.append(_entry(
            "warning", started or created,
            f"自动降档 · {d.get('field')} {d.get('from')} → {d.get('to')}：{d.get('reason', '')}",
        ))

    # ---- 终态 ----------------------------------------------------------------
    if task.status == "failed":
        err = task.error or {}
        code = err.get("code") or "INTERNAL_ERROR"
        msg = err.get("message") or "任务失败"
        entries.append(_entry("error", finished, f"{code} {msg}", code=code))
    elif task.status == "interrupted":
        entries.append(_entry(
            "warning", finished or started or created,
            "进程异常退出，启动回收将任务标记为 interrupted",
        ))
    elif task.status == "canceled":
        entries.append(_entry("info", finished, "任务已取消"))
    elif task.status == "completed":
        parts: list[str] = []
        if task.started_at and task.finished_at:
            ms = int((task.finished_at - task.started_at).total_seconds() * 1000)
            parts.append(f"用时 {ms} ms")
        if execution.get("output_width"):
            parts.append(f"输出 {execution['output_width']}×{execution['output_height']}")
        if execution.get("tiles"):
            parts.append(f"共 {execution['tiles']} 块")
        suffix = (" · " + " · ".join(parts)) if parts else ""
        entries.append(_entry("info", finished, f"任务完成{suffix}"))

    # ---- 正常任务：至少一条 info（不返回空数组）------------------------------
    if not any(e["level"] in ("warning", "error") for e in entries):
        entries.append(_entry(
            "info", finished or started or created,
            "本次任务无警告或错误记录",
        ))

    return entries


def model_name_for(session, task: Task) -> str | None:
    """任务关联模型名（用于日志文案）。"""
    if not task.model_id:
        return None
    m = session.get(Model, task.model_id)
    return m.name if m else None


__all__ = ["derive_logs", "model_name_for"]
