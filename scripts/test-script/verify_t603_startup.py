"""T-603 启动序列验证脚本（开发期验证，非产品代码）。

用法：
    .venvs/sr-app/Scripts/python.exe scripts/test-script/verify_t603_startup.py

场景（全部在临时数据目录中进行，不触碰真实 data/app.db）：
  A. 首次启动：空目录自动完成迁移建库（启动序列第 1 步）
  B. 二次启动：预置 running/canceling 任务被回收为 interrupted（幂等性第三次启动验证）
  C. 数据库不可用：进程拒绝启动并明确报错
"""
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SERVER_DIR = PROJECT_ROOT / "server"
PYTHON = PROJECT_ROOT / ".venvs" / "sr-app" / "Scripts" / "python.exe"

PASS, FAIL = 0, 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" —— {extra}" if extra else ""))


def start_server(env: dict):
    """启动后端子进程，返回 (proc, 已读日志行列表)。调用方负责 terminate。"""
    proc = subprocess.Popen(
        [str(PYTHON), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd=str(SERVER_DIR), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
    )
    return proc


def wait_health(proc, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


def stop_server(proc) -> str:
    proc.terminate()
    try:
        out, _ = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
    return out or ""


def make_env(data_dir: Path, db_url: str) -> dict:
    env = dict(os.environ)
    env.update({
        "APP_DATA_DIR": str(data_dir),
        "APP_DB_URL": db_url,
        "APP_HOST": "127.0.0.1",
        "APP_PORT": "8000",
        "APP_LOG_LEVEL": "info",
    })
    return env


tmp = Path(tempfile.mkdtemp(prefix="websr_t603_"))
data_dir = tmp / "data"
db_url = "sqlite:///" + (data_dir / "app.db").as_posix()
env = make_env(data_dir, db_url)
print(f"临时数据目录: {tmp}")

# ---- 场景 A：首次启动自动迁移建库 ----
proc = start_server(env)
up = wait_health(proc)
log1 = stop_server(proc)
check("A1 首次启动成功（空库自动迁移）", up)
check("A2 迁移日志出现", "数据库迁移完成" in log1)
db_file = data_dir / "app.db"
check("A3 数据库文件已创建", db_file.exists())
if db_file.exists():
    import sqlite3
    # Windows 硬终止服务进程后立刻读 WAL 库可能瞬时 disk I/O error，做短重试
    tables, ver, last_err = set(), None, None
    for _ in range(10):
        try:
            conn = sqlite3.connect(db_file)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            ver = conn.execute("SELECT version_num FROM alembic_version").fetchone()
            conn.close()
            last_err = None
            break
        except sqlite3.OperationalError as exc:
            last_err = exc
            time.sleep(0.5)
    if last_err is not None:
        raise last_err
    check("A4 6 张业务表就位", {"task", "artifact", "model", "preset", "setting", "calibration"} <= tables, str(sorted(tables)))
    check("A5 alembic_version = 0001", ver and ver[0] == "0001", str(ver))

# ---- 预置遗留任务（running / canceling） ----
os.environ.update({"APP_DATA_DIR": str(data_dir), "APP_DB_URL": db_url})
sys.path.insert(0, str(SERVER_DIR))
from app.db import get_session  # noqa: E402
from app.models.entities import Task  # noqa: E402

with get_session() as s:
    s.add(Task(type="upscale", status="running", params={"__t603__": 1}))
    s.add(Task(type="upscale", status="canceling", params={"__t603__": 1}))
    s.commit()

# ---- 场景 B：二次启动回收 interrupted；三次启动幂等 ----
proc = start_server(env)
up2 = wait_health(proc)
log2 = stop_server(proc)
check("B1 二次启动成功", up2)
check("B2 回收日志出现（2 个任务）", "回收上次遗留的 2 个运行中任务" in log2)

with get_session() as s:
    rows = s.query(Task).all()
check("B3 running/canceling 已回收为 interrupted", len(rows) == 2 and all(t.status == "interrupted" for t in rows),
      str([(t.id, t.status) for t in rows]))
check("B4 finished_at 已落库", all(t.finished_at is not None and t.finished_at.tzinfo is not None for t in rows))

proc = start_server(env)
up3 = wait_health(proc)
log3 = stop_server(proc)
check("B5 三次启动幂等（无遗留可回收）", up3 and "无遗留运行中任务" in log3)

check("B6 DLL 注册日志出现（sr-app 无 CUDA/OpenVINO wheel，0 个候选目录为预期）", "DLL 路径注册完成" in log1)

# ---- 场景 C：数据库不可用 → 拒绝启动 ----
bad_env = make_env(data_dir, "sqlite:///" + (tmp / "no_such_parent" / "app.db").as_posix())
proc = start_server(bad_env)
try:
    out, _ = proc.communicate(timeout=30)
except subprocess.TimeoutExpired:
    proc.kill()
    out, _ = proc.communicate()
check("C1 数据库不可用时进程退出（非零码）", proc.returncode not in (0, None), f"returncode={proc.returncode}")
check("C2 明确报错『拒绝启动』", "拒绝启动" in (out or ""))

print(f"\n合计 {PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
