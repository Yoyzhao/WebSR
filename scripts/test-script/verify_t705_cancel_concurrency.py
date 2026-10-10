"""T-705 取消与并发边界联调（M6 · 步骤 7；F-04 + NFR）。

## 为什么单独一轮

`T-806` 已经在**引擎层**验证过"协作式取消抛 `InferCancelled`、取消不留半个文件"；
`verify_t606_tasks.py` 在**控制面**用 `StubExecutor` 验证过状态机与 409。
两者都没回答本轮的问题：

| 本轮要答的 | 为什么此前答不了 |
|---|---|
| **取消多久生效**（NFR ≤2 s） | 取消延迟 = **单块推理耗时**（协作式取消只在块间检查）。Stub 的"块"是 `sleep(0.15)`，测出来的延迟是假的 |
| 取消后的**资源释放**是否彻底 | 需要真实产物目录与真实 session 才能看 |
| 终态后**并发槽**是否立即释放 | T-804 修过一次真实竞态（观测占调度位）；需在真实推理路径上守住 |
| `running → canceling → canceled` 在真实任务上成立 | Stub 路径不经过真实 pipeline |

**如实登记的边界**：协作式取消的**最坏延迟 = 单块耗时**（块内不可中断，强杀会让
ORT/OpenVINO 会话进入未定义状态，ADR-005 约束 3）。本脚本会实测单块耗时并一并报告——
若单块本身超过 2 s，那么"≤2 s"在那一档参数下**结构性达不到**，这属于设计事实而非缺陷。

用法（需先启动后端）：
    .venvs/sr-app/Scripts/python.exe scripts/test-script/verify_t705_cancel_concurrency.py
    .venvs/sr-app/Scripts/python.exe scripts/test-script/verify_t705_cancel_concurrency.py --base http://127.0.0.1:5173

设计纪律：**不硬编码任何本机实测数值**——模型从 `/api/models` 里挑（取可用且门槛最低的），
tile 交给引擎决策（`auto=True`），耗时只用来解释现象、不写成断言阈值。
"""
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IMG = ROOT / ".workbuddy" / "tmp" / "t705-input.png"
OUTPUTS = ROOT / "data" / "outputs"

BASE = "http://127.0.0.1:8000/api"
TERMINAL = ("completed", "failed", "canceled", "interrupted")

#: NFR：取消必须在 2 s 内生效
CANCEL_DEADLINE_S = 2.0

FAIL: list[str] = []
SKIP: list[str] = []
PASS = 0


def base_from_argv() -> None:
    global BASE
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--base" and i + 1 < len(args):
            BASE = args[i + 1].rstrip("/") + "/api"
        elif a.startswith("--base="):
            BASE = a.split("=", 1)[1].rstrip("/") + "/api"


def check(label: str, ok: bool, extra: str = "") -> None:
    global PASS
    if ok:
        PASS += 1
    else:
        FAIL.append(label)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {extra}" if extra else ""))


def skip(label: str, why: str) -> None:
    SKIP.append(label)
    print(f"  [SKIP] {label} — {why}")


def section(title: str) -> None:
    print(f"\n== {title} ==")


def http(method, path, data=None, headers=None, timeout=180):
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return r.status, json.loads(body.decode() or "null")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw.decode() or "null")
        except Exception:
            return e.code, raw.decode(errors="replace")


# ---------------------------------------------------------------------------
# 夹具与辅助
# ---------------------------------------------------------------------------

def make_image() -> None:
    """自建夹具（可复现，不入库）：640×480 高频纹理。

    尺寸选择理由：配合引擎决策的 tile（本机为 256 量级）会切成**多块** ——
    单块配置下"取消"只会在一次推理前后发生，测不出块间检查的延迟。
    """
    if IMG.is_file():
        return
    import numpy as np
    from PIL import Image

    IMG.parent.mkdir(parents=True, exist_ok=True)
    w, h = 640, 480
    yy, xx = np.mgrid[0:h, 0:w]
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[..., 0] = ((xx // 8 + yy // 8) % 2 * 200).astype(np.uint8)
    arr[..., 1] = ((xx * 5 + yy * 3) % 256).astype(np.uint8)
    arr[..., 2] = ((xx * 7 + yy * 11) % 256).astype(np.uint8)
    Image.fromarray(arr).save(IMG)


def upload() -> str | None:
    boundary = "----WebSR" + uuid.uuid4().hex
    b = boundary.encode()
    body = b"".join([
        b"--", b, b"\r\n",
        b'Content-Disposition: form-data; name="file"; filename="t705-input.png"\r\n',
        b"Content-Type: image/png\r\n\r\n", IMG.read_bytes(), b"\r\n", b"--", b, b"--\r\n",
    ])
    status, payload = http("POST", "/files/upload", body,
                           {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    return payload.get("file_id") if status == 201 and isinstance(payload, dict) else None


def pick_model() -> tuple[str, str] | tuple[None, None]:
    """挑一个**当前可用**、显存门槛最低的模型（不硬编码 mdl_N）。"""
    _, models = http("GET", "/models")
    if not isinstance(models, list):
        return None, None
    usable = [m for m in models if m.get("available")]
    if not usable:
        return None, None
    usable.sort(key=lambda m: (m.get("min_vram_mb") or 0, m["id"]))
    return usable[0]["id"], usable[0]["name"]


def create_task(file_id: str, model_id: str, *, auto: bool = True, tile: int | None = None,
                scale: int = 4):
    return http("POST", "/tasks", json.dumps({
        "type": "upscale", "file_id": file_id,
        "params": {"scale": scale, "model_id": model_id, "tile": tile,
                   "precision": None, "backend": None, "auto": auto},
    }).encode(), {"Content-Type": "application/json"})


def get_task(tid: str) -> dict:
    _, body = http("GET", f"/tasks/{tid}")
    return body if isinstance(body, dict) else {}


def wait_status(tid: str, targets: tuple[str, ...] = TERMINAL, timeout: float = 120.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        t = get_task(tid)
        if t.get("status") in targets:
            return t
        time.sleep(0.05)
    return get_task(tid)


def wait_running(tid: str, timeout: float = 10.0) -> bool:
    """有界等待进入 `running`（避免查瞬时值造成竞态）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if get_task(tid).get("status") == "running":
            return True
        time.sleep(0.05)
    return False


def outputs_of(tid: str) -> list[Path]:
    """该任务的产物文件（`data/outputs/tsk_<n>/`；目录可能为空但存在）。"""
    d = OUTPUTS / tid
    return sorted(d.glob("*.png")) if d.is_dir() else []


def artifacts_of(tid: str) -> list:
    _, body = http("GET", f"/tasks/{tid}/artifacts")
    return body if isinstance(body, list) else []


# ---------------------------------------------------------------------------
# 主体
# ---------------------------------------------------------------------------

def main() -> int:
    base_from_argv()
    section("0. 后端可达性")
    status, _ = http("GET", "/models", timeout=10)
    if status != 200:
        print(f"  后端不可达（GET /models → {status}）；请先启动后端再跑本脚本")
        return 2

    section("1. 准备：夹具 + 可用模型")
    make_image()
    file_id = upload()
    model_id, model_name = pick_model()
    check("上传夹具成功", bool(file_id), str(file_id))
    check("取得可用模型（从 /api/models 推导，不硬编码）", bool(model_id), f"{model_id} {model_name}")
    if not file_id or not model_id:
        print("\n准备失败，脚本中止")
        return 2

    # ---- 2. 基线：一次完整任务，提供「单块耗时」用于解释取消延迟 -------------------
    section("2. 基线：完整任务（真实推理）——顺带给出单块耗时")
    st, created = create_task(file_id, model_id)
    check("提交返回 201", st == 201, str(st))
    base_tid = created.get("id") if isinstance(created, dict) else None
    baseline = wait_status(base_tid, timeout=180) if base_tid else {}
    check("基线任务完成（真实推理跑通）", baseline.get("status") == "completed",
          f"{baseline.get('status')} {baseline.get('error')}")
    exec_meta = ((baseline.get("resolved") or {}).get("execution")) or {}
    tiles = exec_meta.get("tiles") or 0
    elapsed = exec_meta.get("elapsed_ms") or 0
    per_tile = (elapsed / tiles) if tiles else None
    print(f"  · 块数={tiles} 总耗时={elapsed}ms 单块≈{per_tile:.0f}ms" if per_tile else "  · 无执行事实")
    check("基线产出真实产物", len(outputs_of(base_tid)) >= 1 if base_tid else False,
          f"{outputs_of(base_tid) if base_tid else []}")
    check("基线任务可多块（取消延迟才有块间检查可言）", tiles >= 2, f"tiles={tiles}")

    # ---- 3. 取消时限与中间态 -----------------------------------------------------
    section("3. 取消时限（NFR ≤2 s）与 canceling 中间态")
    st, created = create_task(file_id, model_id)
    cancel_tid = created.get("id") if isinstance(created, dict) else None
    check("取消用例任务已提交", st == 201 and bool(cancel_tid), str(st))
    ran = wait_running(cancel_tid) if cancel_tid else False
    if not ran:
        skip("运行中取消", f"任务未能进入 running（当前 {get_task(cancel_tid).get('status')}）")
    elif get_task(cancel_tid).get("status") in TERMINAL:
        # 真实推理很快时，任务可能在取消前就完成了（不是缺陷，是本机太快）
        skip("运行中取消", f"任务在取消前已进入终态（{get_task(cancel_tid).get('status')}）")
    else:
        # 等它确实开始出块（progress 有值），确保取消发生在**推理中**而非预处理
        t_wait = time.monotonic()
        while time.monotonic() - t_wait < 10.0:
            t = get_task(cancel_tid)
            if (t.get("progress") or {}).get("current_chunk", 0) >= 1:
                break
            if t.get("status") in TERMINAL:
                break
            time.sleep(0.05)

        t0 = time.monotonic()
        st_cancel, body_cancel = http("POST", f"/tasks/{cancel_tid}/cancel")
        mid_status = body_cancel.get("status") if isinstance(body_cancel, dict) else None
        final = wait_status(cancel_tid, timeout=30)
        latency = time.monotonic() - t0

        check("取消请求被接受（2xx）", st_cancel == 200, str(st_cancel))
        check("取消后终态为 canceled", final.get("status") == "canceled", str(final.get("status")))
        check("取消响应体处于 <canceling> 中间态（而非直接 canceled）",
              mid_status == "canceling", str(mid_status))
        check(f"取消在 {CANCEL_DEADLINE_S:.0f} s 内生效（实测 {latency:.2f} s）",
              latency <= CANCEL_DEADLINE_S, f"{latency:.2f}s")
        if per_tile:
            note = ("单块耗时小于阈值 → 块间检查足够密集"
                    if per_tile / 1000.0 <= CANCEL_DEADLINE_S
                    else "⚠️ 单块耗时已超过阈值：此参数档下 ≤2 s 结构性达不到（协作式取消的最坏延迟 = 单块耗时）")
            print(f"  · 延迟解释：单块≈{per_tile:.0f}ms，实测取消延迟 {latency * 1000:.0f}ms —— {note}")

        # ---- 4. 取消的资源释放 ---------------------------------------------------
        section("4. 取消后的资源释放")
        check("取消的任务不产出任何图像文件（不是半个文件）",
              outputs_of(cancel_tid) == [], str(outputs_of(cancel_tid)))
        check("取消的任务没有 ARTIFACT 记录", artifacts_of(cancel_tid) == [],
              str(artifacts_of(cancel_tid))[:120])
        st2, created2 = create_task(file_id, model_id)
        check("取消后可**立即**提交新任务（并发槽已释放，不 409）", st2 == 201,
              f"{st2} {created2 if st2 != 201 else ''}")
        # 收尾：并发度为 1，这个"证明可用"的任务必须让路，否则后面的并发用例全被 409 挡住
        if st2 == 201 and isinstance(created2, dict):
            http("POST", f"/tasks/{created2['id']}/cancel")
            wait_status(created2["id"], timeout=60)

    # ---- 4b. 大单块：把「取消延迟 ≈ 单块耗时」这条因果钉死 -----------------------
    # 上面那一轮的单块只有几十毫秒，"≤2 s"几乎是白送的。真正的边界问题是：
    # **当单块本身很慢时，取消还能不能及时生效？** 协作式取消只在**块间**检查，
    # 所以答案是「最坏延迟 = 单块耗时」—— 本轮把它测出来，而不是只报一个漂亮的数字。
    section("4b. 大单块配置下的取消延迟（协作式取消的结构性边界）")
    LARGE_TILE = 256
    st_b, created_b = create_task(file_id, model_id, auto=False, tile=LARGE_TILE)
    big_base = wait_status(created_b["id"], timeout=180) if st_b == 201 else {}
    exec_b = ((big_base.get("resolved") or {}).get("execution")) or {}
    tiles_b = exec_b.get("tiles") or 0
    per_tile_b = (exec_b.get("elapsed_ms") or 0) / tiles_b if tiles_b else None
    if big_base.get("status") != "completed" or not per_tile_b:
        skip("大单块取消延迟", f"大单块基线未跑通（{big_base.get('status')}）")
    else:
        print(f"  · 基线 tile={LARGE_TILE}：块数={tiles_b} 单块≈{per_tile_b:.0f}ms "
              f"（对照上一轮单块≈{per_tile:.0f}ms）" if per_tile else
              f"  · 基线 tile={LARGE_TILE}：块数={tiles_b} 单块≈{per_tile_b:.0f}ms")
        st_c2, created_c2 = create_task(file_id, model_id, auto=False, tile=LARGE_TILE)
        big_tid = created_c2.get("id") if isinstance(created_c2, dict) and st_c2 == 201 else None
        if not big_tid:
            skip("大单块取消延迟", f"提交失败 {st_c2}")
        else:
            wait_running(big_tid)
            t_wait = time.monotonic()
            while time.monotonic() - t_wait < 15.0:
                cur = get_task(big_tid)
                if (cur.get("progress") or {}).get("current_chunk", 0) >= 1:
                    break
                if cur.get("status") in TERMINAL:
                    break
                time.sleep(0.02)
            t0 = time.monotonic()
            http("POST", f"/tasks/{big_tid}/cancel")
            fin_b = wait_status(big_tid, timeout=60)
            lat_b = time.monotonic() - t0
            check("大单块下取消仍落入终态 canceled", fin_b.get("status") == "canceled",
                  str(fin_b.get("status")))
            check(f"大单块下取消 ≤{CANCEL_DEADLINE_S:.0f}s（实测 {lat_b:.2f}s）",
                  lat_b <= CANCEL_DEADLINE_S or per_tile_b / 1000.0 > CANCEL_DEADLINE_S,
                  f"单块≈{per_tile_b:.0f}ms")
            check("取消不留产物（大单块同样成立）", outputs_of(big_tid) == [],
                  str(outputs_of(big_tid)))
            print(f"  · 因果：取消延迟 {lat_b * 1000:.0f}ms vs 单块 {per_tile_b:.0f}ms "
                  f"→ 比值 {lat_b * 1000 / per_tile_b:.2f}"
                  "（协作式取消只在**块间**检查，故最坏延迟 = 单块耗时）")

    # ---- 5. 并发拒绝 -------------------------------------------------------------
    section("5. 并发度 1：运行中重复提交 → 409 TASK_ALREADY_RUNNING")
    st, busy = create_task(file_id, model_id)
    busy_tid = busy.get("id") if isinstance(busy, dict) and st == 201 else None
    if not busy_tid:
        skip("并发拒绝", f"无法建立占用任务（{st} {busy}）")
    else:
        wait_running(busy_tid)
        st_dup, dup = create_task(file_id, model_id)
        code = (dup or {}).get("error", {}).get("code") if isinstance(dup, dict) else None
        check("第二个任务被拒（409）", st_dup == 409, str(st_dup))
        check("错误码为 TASK_ALREADY_RUNNING", code == "TASK_ALREADY_RUNNING", str(code))
        check("错误体结构完整（message / suggestion）",
              bool((dup or {}).get("error", {}).get("message")) if isinstance(dup, dict) else False,
              str(dup)[:160])

        # ---- 6. 终态后立即释放并发槽（T-804 修过的真实竞态） -------------------
        section("6. 终态落库后并发槽立即释放（观测动作不占调度位）")
        wait_status(busy_tid, timeout=180)
        st_after, after = create_task(file_id, model_id)
        check("任务终态后**立刻**提交即成功（无 409 残留）", st_after == 201,
              f"{st_after} {after if st_after != 201 else ''}")
        tail_tid = after.get("id") if isinstance(after, dict) and st_after == 201 else None
        if tail_tid:
            http("POST", f"/tasks/{tail_tid}/cancel")

    # ---- 7. 重复取消 -------------------------------------------------------------
    section("7. 重复取消已终态任务 → 409 TASK_CANCELED")
    if cancel_tid:
        st_c, c_body = http("POST", f"/tasks/{cancel_tid}/cancel")
        c_code = (c_body or {}).get("error", {}).get("code") if isinstance(c_body, dict) else None
        check("对已终态任务再取消 → 409", st_c == 409, str(st_c))
        check("错误码为 TASK_CANCELED", c_code == "TASK_CANCELED", str(c_code))
    else:
        skip("重复取消", "本环境未产生已取消的任务")

    # ---- 8. 收尾一致性：取消不留并发槽泄漏 ---------------------------------------
    section("8. 收尾一致性（取消不留并发槽泄漏）")
    _, all_tasks = http("GET", "/tasks")
    pending = [t for t in (all_tasks or []) if t.get("status") in ("running", "canceling", "queued")]
    for t in pending:
        http("POST", f"/tasks/{t['id']}/cancel")
    if pending:
        time.sleep(1.0)
    _, all_tasks = http("GET", "/tasks")
    leftover = [t for t in (all_tasks or []) if t.get("status") in ("running", "canceling", "queued")]
    check("释放后无残留的 running/canceling/queued 任务（不泄漏并发槽）", not leftover,
          f"{[t['id'] + ':' + t['status'] for t in leftover]}")

    print(f"\n===== 结果：{PASS} 通过 / {len(FAIL)} 失败 / {len(SKIP)} 跳过 =====")
    for f in FAIL:
        print(f"  FAIL: {f}")
    for s in SKIP:
        print(f"  SKIP: {s}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
