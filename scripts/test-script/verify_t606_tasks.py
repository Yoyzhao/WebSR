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
from app.tasks import executor as task_executor  # noqa: E402

# 控制面验证不需要真实推理：显式注入 StubExecutor。
# T-806 起生产默认执行器是 EngineExecutor（真实推理），这里换回占位执行器，
# 让状态机 / 取消 / 并发 / 广播的验证保持离线且快（约 1.8s/任务），不依赖模型文件。
task_executor.set_executor_factory(task_executor.StubExecutor)


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
    # 占位执行器**不产出图像**，故产物为空；真实产物链路由 verify_t806_loader.py 覆盖
    check("产物端点可用（占位执行器不产生产物，故为空）", r.status_code == 200 and r.json() == [])

    print("== 7. 删除 / 清理（契约 v1.1：DELETE /api/tasks/{id} + POST /api/tasks/clear-finished）==")

    # 占位执行器**不写产物**，而清理的主要职责正是"连带删产物文件" ——
    # 这里按产物的落盘约定（`outputs/tsk_<id>/`）手工放置文件与记录行。
    # 夹具是"构造出清理要面对的真实状态"，不是把缺陷写进断言。
    def seed_output(task_raw_id: str) -> Path:
        from app.db import get_session
        from app.models.entities import Artifact, Task

        numeric = int(task_raw_id.removeprefix("tsk_"))
        out_dir = DATA / "outputs" / f"tsk_{numeric}"
        out_dir.mkdir(parents=True, exist_ok=True)
        f = out_dir / "a_4x.png"
        f.write_bytes(make_png())
        s = get_session()
        try:
            s.add(Artifact(
                task_id=numeric, kind="output", path=f"outputs/tsk_{numeric}/a_4x.png",
                sha256=None, size_bytes=f.stat().st_size,
            ))
            s.commit()
        finally:
            s.close()
        return f

    # ⚠️ 本节必须用**独立的第二张原图**：第 1~6 节的任务全都引用同一张原图，
    #    若本节继续复用，引用计数永远命中它们，"最后一份引用者"这个场景根本构造不出来
    #    （首版就踩了这个坑——测试预期错，代码是对的）。
    r = client.post("/api/files/upload", files={"file": ("b.png", io.BytesIO(make_png()), "image/png")})
    file2 = r.json()["file_id"]
    up_path = next(iter((DATA / "uploads").glob(f"{file2}.*")))
    thumb = DATA / "thumbs" / f"{file2}.jpg"
    # 第一张原图（第 1~6 节在用）——用来验证"别人的数据不被波及"
    up_path_1 = next(iter((DATA / "uploads").glob(f"{file_id}.*")))

    # 两条任务**共用同一张原图**（同一 file_id 多次提交是常规用法，尤其"产出回灌"迭代）
    r = client.post("/api/tasks", json={"type": "upscale", "file_id": file2, "params": params})
    shared_a = r.json()["id"]
    wait_status(client, shared_a, ("completed",))
    f_a = seed_output(shared_a)
    r = client.post("/api/tasks", json={"type": "upscale", "file_id": file2, "params": params})
    shared_b = r.json()["id"]
    wait_status(client, shared_b, ("completed",))
    f_b = seed_output(shared_b)

    check("夹具就绪：原图 + 缩略图 + 两份产物文件",
          up_path.is_file() and thumb.is_file() and f_a.is_file() and f_b.is_file())

    # ---- 7.1 运行中不可删（删除不得成为绕过取消的后门）----
    r = client.post("/api/tasks", json={"type": "upscale", "file_id": file2, "params": params})
    running = r.json()["id"]
    d = client.delete(f"/api/tasks/{running}")
    check("运行/排队中删除 → 409 TASK_NOT_TERMINAL",
          d.status_code == 409 and d.json()["error"]["code"] == "TASK_NOT_TERMINAL",
          f"{d.status_code} {d.text[:160]}")
    check("被拒后任务仍完好（未被误删）", client.get(f"/api/tasks/{running}").status_code == 200)
    wait_status(client, running, ("completed",))

    d = client.delete("/api/tasks/tsk_99999")
    check("删除不存在的任务 → 404 TASK_NOT_FOUND",
          d.status_code == 404 and d.json()["error"]["code"] == "TASK_NOT_FOUND")
    d = client.delete("/api/tasks/not-an-id")
    check("非法 id → 404（不 500）", d.status_code == 404)

    # ---- 7.2 删一条：文件连带删，但**共享的原图必须留着** ----
    d = client.delete(f"/api/tasks/{shared_a}")
    body = d.json()
    check("删除终态任务 → 200", d.status_code == 200, d.text[:160])
    check("如实回报删除的文件数与字节数",
          body["deleted_files"] >= 1 and body["freed_bytes"] > 0, str(body))
    check("该任务的产物目录随之删除", not f_a.exists())
    check("记录已消失（详情 404）", client.get(f"/api/tasks/{shared_a}").status_code == 404)
    check("⚠️ 原图仍被另一任务引用 → **不删**", up_path.is_file())
    check("缩略图同理保留", thumb.is_file())
    check("另一任务的产出仍可取（result 端点未受影响）",
          client.get(f"/api/files/{file2}/content", params={"variant": "result"}).status_code == 200)

    # ---- 7.3 仍有第三个引用者（7.1 那条）→ 继续保留 ----
    d = client.delete(f"/api/tasks/{shared_b}")
    check("删除第二个引用者 → 200", d.status_code == 200, d.text[:160])
    check("该任务的产物文件已删", not f_b.exists())
    check("⚠️ 还有第三条任务引用该原图 → 仍不删", up_path.is_file())
    check("缩略图同理保留", thumb.is_file())

    # ---- 7.4 删掉**最后一个**引用者：原图/缩略图/旁车元信息一并清理 ----
    d = client.delete(f"/api/tasks/{running}")
    check("删除最后一个引用者 → 200", d.status_code == 200, d.text[:160])
    check("原图已清理", not up_path.exists())
    check("缩略图已清理", not thumb.exists())
    check("旁车元信息已清理", not up_path.with_suffix(".json").exists())
    check("原图端点 → 404（已确属无引用）",
          client.get(f"/api/files/{file2}/content", params={"variant": "original"}).status_code == 404)
    check("result 端点 → 404",
          client.get(f"/api/files/{file2}/content", params={"variant": "result"}).status_code == 404)
    # 别人的数据不受波及：第 1~6 节那张原图仍被它们的任务引用 → 必须原样在
    check("⚠️ 其它任务的原图未被波及", up_path_1.is_file())

    # ---- 7.4b 原图已缺失、旁车仍在 → 旁车同样要清理（删除动作不该有耦合）----
    # 实测来源：真实环境清理时发现"旁车的删除被挂在'原图存在'的分支里"，
    # 原图一旦缺失（历史残留 / 手工清过），`.json` 就成了永远没人认领的残留。
    r = client.post("/api/files/upload", files={"file": ("c.png", io.BytesIO(make_png()), "image/png")})
    file3 = r.json()["file_id"]
    r = client.post("/api/tasks", json={"type": "upscale", "file_id": file3, "params": params})
    orphan = r.json()["id"]
    wait_status(client, orphan, ("completed",))
    up3 = next(iter((DATA / "uploads").glob(f"{file3}.*")))
    side3 = DATA / "uploads" / f"{file3}.json"
    check("夹具：旁车元信息存在", side3.is_file())
    up3.unlink()  # 模拟"原图已缺失"
    d = client.delete(f"/api/tasks/{orphan}")
    check("原图缺失时仍删掉旁车（不因 A 不在就跳过 B）",
          d.status_code == 200 and not side3.exists(),
          f"{d.status_code} sidecar_exists={side3.exists()}")

    # ---- 7.5 批量清理终态任务 ----
    before = client.get("/api/tasks").json()
    terminal_before = [t for t in before
                       if t["status"] in ("completed", "canceled", "failed", "interrupted")]
    r = client.post("/api/tasks/clear-finished")
    body = r.json()
    check("清理端点 200", r.status_code == 200, r.text[:160])
    check("清理条数 == 清理前的终态条数",
          body["cleared"] == len(terminal_before) and body["cleared"] >= 3,
          f"cleared={body['cleared']} terminal_before={len(terminal_before)}")
    check("按状态分类回报且合计一致",
          set(body["by_status"]) <= {"completed", "canceled", "failed", "interrupted"}
          and sum(body["by_status"].values()) == body["cleared"], str(body["by_status"]))
    check("清理后不再有终态任务",
          all(t["status"] not in ("completed", "canceled", "failed", "interrupted")
              for t in client.get("/api/tasks").json()))
    check("删除的文件数/字节数如实回报", body["deleted_files"] >= 1, str(body))

    # ---- 7.6 模型资产不受波及（PRD §315：不得删除模型库中的模型文件）----
    models = client.get("/api/models").json()
    check("模型仍在库中", any(m["id"] == mid for m in models))
    check("模型文件仍在磁盘", len(list((DATA / "models").rglob("*.onnx"))) >= 1)

    again = client.post("/api/tasks/clear-finished").json()
    check("无终态任务时可重复调用（cleared=0，不报错）",
          again["cleared"] == 0 and again["deleted_files"] == 0, str(again))

print(f"\n===== 结果：{PASS} 通过 / {FAIL} 失败 =====")
sys.exit(1 if FAIL else 0)
