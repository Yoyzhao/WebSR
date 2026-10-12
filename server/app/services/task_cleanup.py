"""任务清理（契约 v1.1 新增 `DELETE /api/tasks/{id}` 与 `POST /api/tasks/clear-finished`）。

为什么独立成服务、而不是塞进 `tasks/manager`：这里只做**事后清理**，不参与调度、
不占并发槽、不发 SSE，是纯粹的"删记录 + 删文件"。放进 manager 会让那个已经承载
调度 / 执行 / 序列化的模块再多一类无关职责。

## 语义与边界（逐条可核）

- **只删终态**（`completed` / `canceled` / `failed` / `interrupted`）。运行 / 排队 /
  取消中的任务一律 `409 TASK_NOT_TERMINAL` —— 「删除」不该成为绕过「取消」的后门
  （`04-状态与交互定义 §5` 危险操作保护：取消是协作式的，必须走它自己的通道）。
- **记录与文件一起删**（PRD §315：「清理必须同时删除文件与数据库记录，且**不得删除
  模型库中的模型文件**」）：
  - `outputs/tsk_<id>/` 整个目录（该任务的输出与中间产物）；
  - 产物行指向的文件（万一不在标准目录里，逐条兜底）；
  - `uploads/<file_id>.<ext>` 与 `thumbs/<file_id>.jpg` —— ⚠️ **仅当没有别的任务仍引用
    该 `file_id`** 时才删。同一张原图可被多次提交（`CHG-20261011-07` 起还支持「产出回灌
    为源图」的多轮迭代），无脑删原图会让**别的任务**失去预览与对比。
- **绝不碰 `data/models/`**：模型是用户显式导入的资产（PRD §315），与任务清理无关。
- **路径安全**：每个待删路径都经 `_under_root()` 校验，解析后的绝对路径必须确实落在
  数据根之内 —— `artifact.path` 是从库里读出来的字符串，不能无条件信任。
- **顺序：先删文件、后删记录。** 反过来一旦中途失败，磁盘上会留下"没有任何记录指向"的
  隐形垃圾（正是 `Bug-006` 里那类孤儿文件的来源）；而先删文件失败时记录仍在列表里、
  用户可重试，且报错明确。
"""

from __future__ import annotations

import logging
import shutil
from collections import Counter
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..core.errors import AppError
from ..models.entities import Artifact, Task
from ..tasks.manager import TERMINAL_STATUSES, parse_task_id
from . import media_store, settings_store

logger = logging.getLogger("websr.task_cleanup")

#: 非终态拒绝删除时的界面文案（用户可见：说清"现在为什么不行"）
_STATUS_LABEL = {
    "queued": "排队中",
    "running": "进行中",
    "canceling": "正在取消",
}


# ---------------------------------------------------------------------------
# 路径级删除助手
# ---------------------------------------------------------------------------


def _under_root(p: Path) -> Path | None:
    """解析并校验路径确实落在数据根之内；不合法返回 None（**不抛错**，静默跳过更安全）。"""
    try:
        rp = p.resolve()
        root = settings_store.effective_data_root().resolve()
    except OSError:
        return None
    return rp if rp.is_relative_to(root) else None


def _delete_file(p: Path) -> tuple[int, int]:
    """删单个文件 → (文件数, 字节数)。路径越界 / 不存在都记 0。"""
    rp = _under_root(p)
    if rp is None or not rp.is_file():
        return 0, 0
    try:
        size = rp.stat().st_size
        rp.unlink()
    except OSError as exc:
        logger.warning("产物文件删除失败（已跳过，记录照常清理）：%s %s", rp, exc)
        return 0, 0
    return 1, size


def _delete_dir(d: Path) -> tuple[int, int]:
    """删整棵目录 → (文件数, 字节数)。先统计再删，便于如实回报"释放了多少"。"""
    rp = _under_root(d)
    if rp is None or not rp.is_dir():
        return 0, 0
    files = 0
    total = 0
    for f in rp.rglob("*"):
        if f.is_file():
            files += 1
            try:
                total += f.stat().st_size
            except OSError:
                pass
    try:
        shutil.rmtree(rp)
    except OSError as exc:
        logger.warning("产物目录删除失败（已跳过）：%s %s", rp, exc)
        return 0, 0
    return files, total


def _referenced_file_ids(s: Session, exclude_task_ids: list[int]) -> set[str]:
    """除 `exclude_task_ids` 外，其余任务仍引用的 `file_id` 集合。

    这是"原图能否删"的**唯一判据**：只在 Python 侧遍历（数百级规模），
    不写 `json_extract` 的 SQL —— 少一处方言依赖，逻辑也更好核对。
    """
    excl = set(exclude_task_ids)
    refs: set[str] = set()
    for tid, params in s.execute(select(Task.id, Task.params)).all():
        if tid in excl:
            continue
        fid = (params or {}).get("file_id")
        if fid:
            refs.add(str(fid))
    return refs


# ---------------------------------------------------------------------------
# 主体
# ---------------------------------------------------------------------------


def _erase(s: Session, tasks: list[Task], keep_file_ids: set[str]) -> tuple[int, int, dict[str, int]]:
    """删掉这批任务的文件与记录 → (文件数, 字节数, 按状态计数)。

    调用方负责：① 保证 `tasks` 全为终态；② 传入正确的 `keep_file_ids`（共享原图判定）。
    """
    files = 0
    freed = 0
    by_status: Counter[str] = Counter()
    file_ids: set[str] = set()

    for t in tasks:
        by_status[t.status] += 1
        fid = (t.params or {}).get("file_id")
        if fid:
            file_ids.add(str(fid))

        n, b = _delete_dir(media_store.task_outputs_path(t.id))
        files += n
        freed += b

        # 兜底：产物行指向的文件可能落在标准目录之外（历史数据 / 手工放置）
        for a in s.scalars(select(Artifact).where(Artifact.task_id == t.id)).all():
            n, b = _delete_file(settings_store.effective_data_root() / a.path)
            files += n
            freed += b

    # 原图与缩略图：只有**不再被任何任务引用**才删（否则别的任务会失去预览/对比）
    for fid in sorted(file_ids - keep_file_ids):
        # 三样东西**各自独立清理**（2026-10-11 修正）：早先版本把旁车 `.json` 的删除
        # 挂在"原图存在"这个分支里 —— 原图已被删（或从未落盘）而旁车还在时，
        # 旁车就成了永远没人认领的残留。删除动作不该有"因为 A 不在所以连 B 也不看"的耦合。
        up = media_store.find_upload(fid, create=False)
        if up is not None:
            n, b = _delete_file(up)
            files += n
            freed += b
        # 旁车元信息（上传不落库，文件名/尺寸靠它反查）—— 与产物一同清理
        n, b = _delete_file(media_store.upload_meta_path(fid, create=False))
        files += n
        freed += b
        n, b = _delete_file(media_store.thumb_path(fid, create=False))
        files += n
        freed += b

    # 记录：显式删产物行再删任务行。
    # ⚠️ **不能依赖 `ON DELETE CASCADE`** —— 迁移期 `PRAGMA foreign_keys=OFF` 下 CASCADE
    #    不生效，会静默留下产物行（与 `settings_store.cleanup_expired_tasks` 同一处置）。
    ids = [t.id for t in tasks]
    s.execute(delete(Artifact).where(Artifact.task_id.in_(ids)))
    s.execute(delete(Task).where(Task.id.in_(ids)))
    s.commit()

    return files, freed, dict(by_status)


def delete_task(s: Session, raw_id: str) -> dict:
    """删除**单个**终态任务（记录 + 其文件）。返回契约 v1.1 的 `TaskDeleteOut`。"""
    tid = parse_task_id(raw_id)
    t = s.get(Task, tid)
    if t is None:
        raise AppError("TASK_NOT_FOUND", "任务不存在", "请刷新任务列表后重试", 404)
    if t.status not in TERMINAL_STATUSES:
        raise AppError(
            "TASK_NOT_TERMINAL",
            f"任务{_STATUS_LABEL.get(t.status, t.status)}，暂时不能删除",
            "请先取消该任务，等它进入终态后再删除",
            409,
            detail={"status": t.status},
        )

    files, freed, _ = _erase(s, [t], keep_file_ids=_referenced_file_ids(s, [tid]))
    return {"id": f"tsk_{tid}", "deleted_files": files, "freed_bytes": freed}


def clear_finished(s: Session) -> dict:
    """清理**全部终态任务**（`completed` / `canceled` / `failed` / `interrupted`）。

    为什么不是字面的"只清 `completed`"：界面用词沿用 5A 定稿的「清理已完成」，
    但用户要清的是"跑完的、不再需要的"记录 —— 失败与取消同样属于这一类。
    为了不做出让人意外的删除，接口**按状态分类回报条数**，前端在二次确认里逐类列出。
    运行 / 排队 / 取消中的任务**永不清理**（无参数可绕过）。
    """
    rows = list(s.scalars(select(Task).where(Task.status.in_(TERMINAL_STATUSES))).all())
    if not rows:
        return {"cleared": 0, "by_status": {}, "deleted_files": 0, "freed_bytes": 0}

    keep = _referenced_file_ids(s, [t.id for t in rows])
    files, freed, by_status = _erase(s, rows, keep_file_ids=keep)
    logger.info("清理终态任务 %d 条、文件 %d 个，释放 %d 字节", len(rows), files, freed)
    return {
        "cleared": len(rows),
        "by_status": by_status,
        "deleted_files": files,
        "freed_bytes": freed,
    }
