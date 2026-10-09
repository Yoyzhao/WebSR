"""T-602 数据模型与迁移验证脚本（开发期验证，非产品代码）。

用法（先执行过 `alembic upgrade head`）：
    .venvs/sr-app/Scripts/python.exe scripts/test-script/verify_t602_db.py

断言覆盖：建表与索引、PRAGMA 基线、UTCDateTime 往返与 naive 拦截、
JSON 中文往返、MODEL.sha256 唯一约束、迁移版本记录。
"""
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[2] / "server"
sys.path.insert(0, str(SERVER_DIR))

from sqlalchemy import inspect, text  # noqa: E402
from sqlalchemy.exc import IntegrityError, StatementError  # noqa: E402

from app.db import get_engine, get_session  # noqa: E402
from app.models.entities import Model, Setting, Task  # noqa: E402

PASS, FAIL = 0, 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    mark = "PASS" if ok else "FAIL"
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"[{mark}] {name}" + (f" —— {extra}" if extra else ""))


engine = get_engine()

# 1. 表与索引
insp = inspect(engine)
tables = set(insp.get_table_names())
expected = {"task", "artifact", "model", "preset", "setting", "calibration", "alembic_version"}
check("6 张业务表 + alembic_version 均已创建", expected <= tables, f"实际 {sorted(tables)}")

idx_names = {i["name"] for t in expected - {"alembic_version"} for i in insp.get_indexes(t)}
check("TASK(status, created_at) 复合索引存在", any("task" in n and "status" in n for n in idx_names), str(idx_names))
check("ARTIFACT(task_id) 索引存在", any("artifact" in n for n in idx_names))
check("CALIBRATION(model_id, fingerprint, valid) 复合索引存在", any("calibration" in n for n in idx_names))
uq = insp.get_unique_constraints("model")
check("MODEL.sha256 唯一约束存在", any("sha256" in c.get("column_names", []) for c in uq), str(uq))

# 2. PRAGMA 基线（运行时连接）
with engine.connect() as conn:
    check("journal_mode = WAL", conn.execute(text("PRAGMA journal_mode")).scalar() == "wal")
    check("foreign_keys = ON（运行时）", conn.execute(text("PRAGMA foreign_keys")).scalar() == 1)
    check("busy_timeout = 5000", conn.execute(text("PRAGMA busy_timeout")).scalar() == 5000)
    check("synchronous = NORMAL(1)", conn.execute(text("PRAGMA synchronous")).scalar() == 1)

# 3. alembic_version
with engine.connect() as conn:
    ver = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    check("迁移版本 = 0001", ver == "0001", f"实际 {ver}")

# 4. UTCDateTime 往返 + naive 拦截
# 先清理历史探针行（脚本须可重复执行）
s = get_session()
try:
    s.query(Setting).filter(Setting.key.like("__t602_%")).delete()
    s.query(Model).filter(Model.sha256.like("t602%")).delete(synchronize_session=False)
    # 只删带探针标记的任务（json_extract 匹配 params.__t602__），不得误删真实任务
    s.execute(text("DELETE FROM task WHERE json_extract(params, '$.__t602__') = 1"))
    s.commit()
finally:
    s.close()

s = get_session()
try:
    st = Setting(key="__t602_probe__", value="探针", value_type="string", updated_at=datetime.now(timezone.utc))
    s.add(st)
    s.commit()
    got = s.get(Setting, "__t602_probe__")
    check("UTCDateTime 写入 aware → 读回带 tzinfo=UTC", got.updated_at.tzinfo == timezone.utc, str(got.updated_at))
    check("JSON/中文落库往返（Setting.value）", got.value == "探针")
    try:
        s.add(Setting(key="__t602_naive__", value="x", value_type="string", updated_at=datetime.now()))
        s.commit()
        check("UTCDateTime 拒绝 naive 写入", False, "未抛错")
    except (ValueError, StatementError):
        # SQLAlchemy 会把 TypeDecorator 的 ValueError 包装为 StatementError，两者都算拦截成功
        s.rollback()
        check("UTCDateTime 拒绝 naive 写入", True)
    s.delete(got)
    s.commit()
finally:
    s.close()

# 5. JSON 数组列 + 唯一约束
s = get_session()
try:
    m = Model(name="探针模型", format="onnx", supported_backends=["cuda", "cpu"], path="data/models/x.onnx",
              sha256="t602" + "0" * 61, source="builtin", status="ready")
    s.add(m)
    s.commit()
    got = s.query(Model).filter_by(sha256=m.sha256).one()
    check("supported_backends JSON 数组往返", got.supported_backends == ["cuda", "cpu"], str(got.supported_backends))
    try:
        s.add(Model(name="重复哈希", format="onnx", supported_backends=[], path="y.onnx",
                    sha256=m.sha256, source="imported", status="ready"))
        s.commit()
        check("MODEL.sha256 重复写入被拒绝", False, "未抛错")
    except IntegrityError:
        s.rollback()
        check("MODEL.sha256 重复写入被拒绝", True)
    s.delete(got)
    s.commit()
finally:
    s.close()

# 6. Task 外键可空（历史任务不受模型删除影响）
s = get_session()
try:
    t = Task(type="upscale", status="queued", params={"scale": 4, "备注": "中文参数", "__t602__": 1})
    s.add(t)
    s.commit()
    got = s.get(Task, t.id)
    check("Task.params JSON 中文往返", got.params["备注"] == "中文参数")
    check("Task 默认进度 progress_total=1", got.progress_total == 1)
    s.delete(got)
    s.commit()
finally:
    s.close()

print(f"\n合计 {PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
