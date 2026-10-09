"""T-607 SSE 进度推送验证：snapshot / progress / done / ping / 断线恢复 / 帧格式。

运行：./.venvs/sr-app/Scripts/python.exe scripts/test-script/verify_t607_sse.py
"""
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="websr_t607_"))
DATA = TMP / "data"

os.environ["APP_DATA_DIR"] = str(DATA)
os.environ["APP_DB_URL"] = f"sqlite:///{(DATA / 'app.db').as_posix()}"

sys.path.insert(0, str(ROOT / "server"))

PASS = FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app.main import app  # noqa: E402
import app.api.events as events_mod  # noqa: E402
from app.tasks import executor as task_executor  # noqa: E402

# 控制面验证不需要真实推理：显式注入 StubExecutor（T-806 起生产默认是 EngineExecutor）
task_executor.set_executor_factory(task_executor.StubExecutor)

events_mod.PING_INTERVAL_SECONDS = 0.3  # 加速心跳验证


def make_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (320, 240), (10, 120, 200)).save(buf, "PNG")
    return buf.getvalue()


def parse_sse(lines):
    """[(event, data_dict), ...]"""
    out, event = [], None
    for line in lines:
        if line.startswith("event: "):
            event = line[7:].strip()
        elif line.startswith("data: ") and event:
            out.append((event, json.loads(line[6:])))
            event = None
    return out


with TestClient(app) as client:
    r = client.post("/api/files/upload", files={"file": ("a.png", io.BytesIO(make_png()), "image/png")})
    file_id = r.json()["file_id"]
    r = client.post("/api/models/import", data={
        "name": "SR_x4", "format": "onnx", "scale": "4",
        "min_vram_mb": "0", "backends": json.dumps(["cpu"]),
    }, files={"file": ("m.onnx", io.BytesIO(b"onnx"), "application/octet-stream")})
    mid = r.json()["id"]
    params = {"scale": 4, "model_id": mid, "tile": None, "precision": None, "backend": None, "auto": True}

    print("== 1. 运行中订阅：snapshot → progress → done ==")
    r = client.post("/api/tasks", json={"type": "upscale", "file_id": file_id, "params": params})
    tid = r.json()["id"]

    with client.stream("GET", f"/api/tasks/{tid}/events") as resp:
        check("Content-Type 为 text/event-stream",
              resp.headers.get("content-type", "").startswith("text/event-stream"))
        frames = parse_sse(resp.iter_lines())

    types = [e for e, _ in frames]
    check("首帧是 snapshot", types[0] == "snapshot", f"actual={types[:3]}")
    check("含 progress 帧", "progress" in types)
    check("含 ping 心跳帧", "ping" in types)
    check("末帧是 done", types[-1] == "done")
    check("snapshot 在 progress 之前", types.index("snapshot") < types.index("progress"))

    snap = frames[0][1]["task"]
    check("snapshot 是完整 Task", all(k in snap for k in ("id", "status", "progress", "params")))
    prog = [d for e, d in frames if e == "progress"]
    required = {"task_id", "percent", "current_item", "total_items",
                "current_chunk", "total_chunks", "stage", "message"}
    check("progress 字段齐全（契约 §5）", all(required <= set(p) for p in prog))
    percents = [p["percent"] for p in prog]
    check("percent 单调不减", all(b >= a for a, b in zip(percents, percents[1:])),
          f"actual={percents}")
    done_payload = frames[-1][1]["task"]
    check("done 帧含终态 Task", done_payload["status"] == "completed"
          and done_payload["resolved"] is not None)

    print("== 2. 终态后订阅（断线恢复路径）==")
    with client.stream("GET", f"/api/tasks/{tid}/events") as resp:
        frames2 = parse_sse(resp.iter_lines())
    types2 = [e for e, _ in frames2]
    check("终态订阅 = snapshot + done 即关", types2 == ["snapshot", "done"], f"actual={types2}")
    check("恢复快照状态为终态", frames2[0][1]["task"]["status"] == "completed")

    print("== 3. 任务不存在 ==")
    r = client.get("/api/tasks/tsk_99999/events")
    check("404 TASK_NOT_FOUND（JSON 错误体而非 SSE）",
          r.status_code == 404 and r.json()["error"]["code"] == "TASK_NOT_FOUND")

    print("== 4. 取消路径的 SSE 终止帧 ==")
    r = client.post("/api/tasks", json={"type": "upscale", "file_id": file_id, "params": params})
    tid2 = r.json()["id"]
    time.sleep(0.4)
    client.post(f"/api/tasks/{tid2}/cancel")
    with client.stream("GET", f"/api/tasks/{tid2}/events") as resp:
        frames3 = parse_sse(resp.iter_lines())
    check("取消后 done 帧状态为 canceled", frames3[-1][1]["task"]["status"] == "canceled",
          f"actual={frames3[-1][1]['task']['status']}")

print(f"\n===== 结果：{PASS} 通过 / {FAIL} 失败 =====")
sys.exit(1 if FAIL else 0)
