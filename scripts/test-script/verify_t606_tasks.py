"""T-606 任务中心（M1）验证：提交 / 状态机 / 协作式取消 / 并发 1 / 门控 / 广播。

执行器为 StubExecutor（12 块 × 150ms ≈ 1.8s），只验证控制面；
真实推理由 T-808 接入。隔离策略同 T-604/T-605（临时数据根）。
运行：./.venvs/sr-app/Scripts/python.exe scripts/test-script/verify_t606_tasks.py
"""
import io
import json
import os
import queue
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="websr_t606_"))
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
from app.tasks.broadcaster import broadcaster  # noqa: E402


def make_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (320, 240), (10, 120, 200)).save(buf, "PNG")
    return buf.getvalue()


def wait_status(client, task_id: str, targets: tuple[str, ...], timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = client.get(f"/api/tasks/{task_id}").json()
        if t["status"] in targets:
            return t
        time.sleep(0.2)
    raise TimeoutError(f"{task_id} 未在 {timeout}s 内进入 {targets}")


with TestClient(app) as client:
    print("== 0. 准备：上传图片 + 导入可用模型 ==")
    r = client.post("/api/files/upload", files={"file": ("a.png", io.BytesIO(make_png()), "image/png")})
    file_id = r.json()["file_id"]
    check("上传就绪", r.status_code == 201)

    def import_model(name, payload, fmt="onnx", min_vram=0, filename="m.onnx"):
        return client.post("/api/models/import", data={
            "name": name, "format": fmt, "scale": "4",
            "min_vram_mb": str(min_vram), "backends": json.dumps(["cpu"]),
        }, files={"file": (filename, io.BytesIO(payload), "application/octet-stream")})

    mid = import_model("SR_x4", b"onnx-weights").json()["id"]
    check("模型就绪", mid.startswith("mdl_"))

    params = {"scale": 4, "model_id": mid, "tile": None, "precision": None, "backend": None, "auto": True}

    print("== 1. 提交 → 完成 全链路 ==")
    q = broadcaster.subscribe("pending")  # 占位订阅验证稍后替换
    r = client.post("/api/tasks", json={"type": "upscale", "file_id": file_id, "params": params})
    check("提交 → 201", r.status_code == 201, f"actual={r.status_code} {r.text[:200]}")
    task = r.json()
    tid = task["id"]
    check("id 带 tsk_ 前缀", tid.startswith("tsk_"))
    check("初始状态 queued/running", task["status"] in ("queued", "running"))
    check("file_id/filename/尺寸快照", task["file_id"] == file_id and task["filename"] == "a.png"
          and task["source_width"] == 320 and task["source_height"] == 240)

    done = wait_status(client, tid, ("completed", "failed"))
    check("任务完成", done["status"] == "completed", f"error={done.get('error')}")
    check("percent=1.0", done["progress"]["percent"] == 1.0)
    check("resolved.using_fallback（保底档）", done["resolved"]["using_fallback"] is True)
    check("duration_ms 已记录", isinstance(done["duration_ms"], int) and done["duration_ms"] >= 1000)
    check("时间戳齐全", done["started_at"] and done["finished_at"])

    print("== 2. 广播事件（进度 + 终止）==")
    events_q = None
    r = client.post("/api/tasks", json={"type": "upscale", "file_id": file_id, "params": params})
    tid2 = r.json()["id"]
    events_q = broadcaster.subscribe(tid2)
    seen_types = set()
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            ev = events_q.get(timeout=0.5)
            seen_types.add(ev["type"])
            if ev["type"] == "done":
                break
        except queue.Empty:
            if client.get(f"/api/tasks/{tid2}").json()["status"] == "completed":
                break
    check("收到 progress 事件", "progress" in seen_types, f"seen={seen_types}")
    check("收到 done 事件（含完整 Task）", "done" in seen_types)

    print("== 3. 协作式取消 ==")
    r = client.post("/api/tasks", json={"type": "upscale", "file_id": file_id, "params": params})
    tid3 = r.json()["id"]
    time.sleep(0.4)  # 确保进入 running
    r = client.post(f"/api/tasks/{tid3}/cancel")
    check("取消受理（canceling/canceled）", r.status_code == 200 and r.json()["status"] in ("canceling", "canceled"),
          f"actual={r.status_code} {r.text[:200]}")
    final = wait_status(client, tid3, ("canceled", "completed"))
    check("最终进入 canceled（非 completed）", final["status"] == "canceled")
    r = client.post(f"/api/tasks/{tid3}/cancel")
    check("重复取消 → 409 TASK_CANCELED",
          r.status_code == 409 and r.json()["error"]["code"] == "TASK_CANCELED")

    print("== 4. 并发度 1 ==")
    r = client.post("/api/tasks", json={"type": "upscale", "file_id": file_id, "params": params})
    tid4 = r.json()["id"]
    r = client.post("/api/tasks", json={"type": "upscale", "file_id": file_id, "params": params})
    check("运行中重复提交 → 409 TASK_ALREADY_RUNNING",
          r.status_code == 409 and r.json()["error"]["code"] == "TASK_ALREADY_RUNNING")
    wait_status(client, tid4, ("completed",))

    print("== 5. 提交门控 ==")
    pth_id = import_model("RawPTH", b"torch", fmt="pth", filename="m.pth").json()["id"]
    r = client.post("/api/tasks", json={
        "type": "upscale", "file_id": file_id,
        "params": {**params, "model_id": pth_id}})
    check("needs_convert 模型 → 400 MODEL_INCOMPATIBLE",
          r.status_code == 400 and r.json()["error"]["code"] == "MODEL_INCOMPATIBLE")

    big_id = import_model("HugeSR", b"onnx-huge", min_vram=999999).json()["id"]
    r = client.post("/api/tasks", json={
        "type": "upscale", "file_id": file_id,
        "params": {**params, "model_id": big_id}})
    if r.status_code == 409:
        check("显存门槛 → 409 MODEL_INSUFFICIENT_VRAM",
              r.json()["error"]["code"] == "MODEL_INSUFFICIENT_VRAM")
    else:
        # 探测不到显存的环境不门控（与 availability 语义一致）
        check("显存门槛（本环境探测不到显存，不门控）", r.status_code == 201)
        wait_status(client, r.json()["id"], ("completed",))

    r = client.post("/api/tasks", json={
        "type": "upscale", "file_id": file_id,
        "params": {**params, "model_id": "mdl_99999"}})
    check("模型不存在 → 404 MODEL_NOT_FOUND",
          r.status_code == 404 and r.json()["error"]["code"] == "MODEL_NOT_FOUND")
    r = client.post("/api/tasks", json={
        "type": "upscale", "file_id": "file_deadbeef99", "params": params})
    check("文件不存在 → 404", r.status_code == 404)
    r = client.post("/api/tasks", json={"type": "video", "file_id": file_id, "params": params})
    check("未开放类型 → 400 VALIDATION_ERROR",
          r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR")

    print("== 6. 列表 / 详情 / 产物 ==")
    r = client.get("/api/tasks")
    check("列表返回数组", isinstance(r.json(), list) and len(r.json()) >= 4)
    check("列表按时间倒序", r.json()[0]["created_at"] >= r.json()[-1]["created_at"])
    r = client.get("/api/tasks", params={"status": "canceled"})
    check("status 筛选", all(t["status"] == "canceled" for t in r.json()) and len(r.json()) >= 1)
    r = client.get("/api/tasks/tsk_99999")
    check("详情 404 TASK_NOT_FOUND",
          r.status_code == 404 and r.json()["error"]["code"] == "TASK_NOT_FOUND")
    r = client.get(f"/api/tasks/{tid}/artifacts")
    check("产物列表（当前为空，T-808 回填）", r.status_code == 200 and r.json() == [])

print(f"\n===== 结果：{PASS} 通过 / {FAIL} 失败 =====")
sys.exit(1 if FAIL else 0)
