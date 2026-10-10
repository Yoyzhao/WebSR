"""T-702 前后端整合 E2E（S1 全链路 · 真实推理路径）。

⚠️ 这是**唯一覆盖「真实推理路径 + 真实 SSE + 真实产物」的端到端脚本**：
   其余验证脚本注入 `StubExecutor`（控制面验证与真实推理解耦），因此**看不到**
   真实执行器发出来的进度语义 —— T-702 的 `percent` 非单调缺陷正是这样漏过 5C/5D 的。

用法（需先启动后端；若要连浏览器侧链路，再启动前端并把 base 指向 :5173）：
    .venvs/sr-app/Scripts/python.exe scripts/test-script/verify_t702_e2e.py
    .venvs/sr-app/Scripts/python.exe scripts/test-script/verify_t702_e2e.py --base http://127.0.0.1:5173

覆盖：
  1  上传 → 建任务 → SSE(snapshot/progress/done) → 终态
  2  progress 契约：字段齐全、**percent 单调不减**、预处理阶段不得假 100%
  3  终态任务字段：output 尺寸 / duration / resolved 分层 / execution 执行事实
  4  产物：art_ 前缀、data/ 口径、尺寸、sha256
  5  日志端点
  6  文件内容 original / thumb / **result**
  7  异常链路：受控 4xx 统一错误体 + 服务仍可用
  8  标定消费口径：命中记录时 tile 必须**等于该记录显式给出的 recommended_tile**
     （环境相关值一律**从接口推导**，不硬编码本机实测值）
  9  经代理 SSE 必须为**流式**（base 指向 Vite :5173 时自动执行）

设计纪律：**不硬编码任何本机实测数值**（标定推荐 tile 从 `/api/system/calibration`
读取；保底档取值只断言"走保底档"这一事实，不复述具体数字）。
"""
import json
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IMG = ROOT / ".workbuddy" / "tmp" / "e2e-input.png"

BASE = "http://127.0.0.1:8000/api"
FAIL, PASS, SKIP = [], 0, []


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
    SKIP.append(f"{label}（{why}）")
    print(f"  [SKIP] {label} — {why}")


def http(method, path, data=None, headers=None, raw=False, timeout=180):
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            hdrs = {k.lower(): v for k, v in r.headers.items()}
            return r.status, (body if raw else json.loads(body.decode() or "null")), hdrs
    except urllib.error.HTTPError as e:
        raw_body = e.read()
        hdrs = {k.lower(): v for k, v in e.headers.items()}
        try:
            return e.code, json.loads(raw_body.decode() or "null"), hdrs
        except Exception:
            return e.code, raw_body, hdrs


def make_test_image() -> None:
    """生成一张小尺寸、含高频纹理的测试图（稳定的固定内容，便于比对体积）。"""
    if IMG.is_file():
        return
    import numpy as np
    from PIL import Image

    IMG.parent.mkdir(parents=True, exist_ok=True)
    w, h = 320, 240
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    yy, xx = np.mgrid[0:h, 0:w]
    arr[..., 0] = ((xx // 8 + yy // 8) % 2 * 200).astype(np.uint8)
    arr[..., 1] = ((xx * 5 + yy * 3) % 256).astype(np.uint8)
    arr[..., 2] = ((xx * 7 + yy * 11) % 256).astype(np.uint8)
    Image.fromarray(arr).save(IMG)


def upload(name="t702.png"):
    boundary = "----WebSR" + uuid.uuid4().hex
    b = boundary.encode()
    body = b"".join([
        b"--", b, b"\r\n",
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode(),
        b"Content-Type: image/png\r\n\r\n", IMG.read_bytes(), b"\r\n", b"--", b, b"--\r\n",
    ])
    return http("POST", "/files/upload", body,
                {"Content-Type": f"multipart/form-data; boundary={boundary}"})


def create_task(file_id, model_id):
    return http("POST", "/tasks", json.dumps({
        "type": "upscale", "file_id": file_id,
        "params": {"scale": 4, "model_id": model_id, "tile": None,
                   "precision": None, "backend": None, "auto": True}}).encode(),
        {"Content-Type": "application/json"})


def sse_collect(task_id, sink, stop, t0):
    """后台线程读 SSE，收集 (event, data, 相对到达秒)。"""
    try:
        resp = urllib.request.urlopen(f"{BASE}/tasks/{task_id}/events", timeout=180)
    except Exception as e:  # noqa: BLE001
        sink.append(("__error__", str(e), round(time.monotonic() - t0, 3)))
        return
    event, buf = None, []
    try:
        for line in resp:
            if stop.is_set():
                break
            s = line.decode().rstrip("\r\n")
            if s == "":
                if event is not None:
                    try:
                        payload = json.loads("".join(buf)) if buf else {}
                    except Exception:
                        payload = {"__raw__": "".join(buf)}
                    sink.append((event, payload, round(time.monotonic() - t0, 3)))
                event, buf = None, []
            elif s.startswith("event:"):
                event = s[6:].strip()
            elif s.startswith("data:"):
                buf.append(s[5:].strip())
    finally:
        resp.close()


def run_stream(file_id, model_id):
    """建任务 → 订阅 SSE → 等终态；返回 (终态任务, 帧列表)。"""
    st, task, _ = create_task(file_id, model_id)
    assert st in (200, 201), task
    frames, stop, t0 = [], threading.Event(), time.monotonic()
    th = threading.Thread(target=sse_collect, args=(task["id"], frames, stop, t0), daemon=True)
    th.start()
    final, deadline = task, time.time() + 180
    while time.time() < deadline:
        st, t, _ = http("GET", f"/tasks/{task['id']}")
        if st == 200:
            final = t
        if t["status"] in ("completed", "failed", "canceled", "interrupted"):
            break
        time.sleep(0.3)
    time.sleep(0.8)
    stop.set()
    th.join(timeout=3)
    return final, frames


def main() -> None:
    base_from_argv()
    print(f"== T-702 前后端整合 E2E（base={BASE}）==")
    make_test_image()

    st, _, _ = http("GET", "/system/capabilities", timeout=10)
    if st != 200:
        print(f"\n❌ 后端不可达（{BASE}/system/capabilities → HTTP {st}）。")
        print("   请先启动后端： cd server && ../.venvs/sr-app/Scripts/python.exe -m uvicorn app.main:app --port 8000")
        sys.exit(2)

    models = http("GET", "/models")[1]
    available = [m for m in models if m.get("available")]
    check("模型库有可用模型", bool(available), f"{len(available)}/{len(models)} 可用")

    calib = http("GET", "/system/calibration")[1] or {}
    recs = calib.get("records") or []
    calibrated_ids = {f"mdl_{r['model_id']}" for r in recs if r.get("model_id") is not None}
    print(f"    标定记录 {len(recs)} 条 → 已标定模型 {sorted(calibrated_ids) or '（无）'}")

    uncal = next((m for m in available if m["id"] not in calibrated_ids), None)
    cal = next((m for m in available if m["id"] in calibrated_ids), None)

    # ---- 1/2/3. 上传 + 建任务 + SSE 全链路 ----
    print("\n[1-3] 上传 → 建任务 → SSE → 终态（未标定模型）")
    st, up, _ = upload()
    check("上传 200/201 且返回 file_id", st in (200, 201) and up.get("file_id"),
          f"HTTP {st} {up.get('file_id')}")
    file_id = up["file_id"]
    check("上传返回真实宽高", up.get("width") == 320 and up.get("height") == 240,
          f"{up.get('width')}x{up.get('height')}")

    probe_model = (uncal or available[0])["id"]
    final, frames = run_stream(file_id, probe_model)
    kinds = [e for e, _d, _t in frames]
    print(f"    模型 {probe_model}；SSE 帧序 {[(e, d.get('stage') or d.get('task', {}).get('status', '')) for e, d, _t in frames]}")
    check("首帧 snapshot", kinds and kinds[0] == "snapshot", str(kinds[:3]))
    check("末帧 done", kinds and kinds[-1] == "done", str(kinds[-1:]))
    check("无 SSE 传输错误", "__error__" not in kinds)
    check("终态 completed", final["status"] == "completed", final["status"])

    progs = [d for e, d, _t in frames if e == "progress"]
    required = {"task_id", "percent", "current_item", "total_items",
                "current_chunk", "total_chunks", "stage", "message"}
    check("progress 字段齐全（契约 §5）", all(required <= set(d) for d in progs))
    pcts = [d["percent"] for d in progs]
    check("🔴 percent 单调不减（契约 §5 / NFR §3.1）",
          all(b >= a for a, b in zip(pcts, pcts[1:])), f"actual={pcts}")
    check("percent 终值 = 1.0", bool(pcts) and pcts[-1] == 1.0, str(pcts[-1] if pcts else None))
    pre = [d["percent"] for d in progs if d.get("stage") == "preprocessing"]
    check("🔴 预处理阶段不得假 100%（T-702 缺陷回归）", all(p < 1.0 for p in pre), f"actual={pre}")
    check("done 帧携带完整终态任务", frames[-1][1]["task"]["status"] == "completed")

    print("\n[4] 终态任务字段")
    check("output = 4× 源（1280x960）",
          final.get("output_width") == 1280 and final.get("output_height") == 960,
          f"{final.get('output_width')}x{final.get('output_height')}")
    check("duration_ms 为整数", isinstance(final.get("duration_ms"), int), str(final.get("duration_ms")))
    check("时间三件套齐备", all(final.get(k) for k in ("created_at", "started_at", "finished_at")))
    res = final.get("resolved") or {}
    check("resolved 为决策事实层（含 backend/precision/tile/source）",
          all(k in res for k in ("backend", "precision", "tile", "source", "using_fallback")),
          f"source={res.get('source')} tile={res.get('tile')} backend={res.get('backend')}")
    check("resolved.reasons 非空（必须解释取值来源）", bool(res.get("reasons")))
    ex = res.get("execution") or {}
    check("resolved.execution 为执行事实层（tiles/elapsed/providers）",
          ex.get("tiles") and ex.get("elapsed_ms") is not None
          and (ex.get("backend") or {}).get("providers"),
          f"tiles={ex.get('tiles')} providers={(ex.get('backend') or {}).get('providers')}")
    check("degraded 与 downgrades 一致",
          bool(res.get("degraded")) == bool(res.get("downgrades")),
          f"degraded={res.get('degraded')} downgrades={res.get('downgrades')}")
    check("ep_evidence 字段存在（契约：S1 恒空，回填属后续增强）",
          isinstance(final.get("ep_evidence"), list), str(final.get("ep_evidence")))

    print("\n[5] 产物")
    outs = [a for a in (final.get("artifacts") or []) if a.get("kind") == "output"]
    check("存在 output 产物", bool(outs))
    if outs:
        check("产物 id 前缀 art_", outs[0]["id"].startswith("art_"), outs[0]["id"])
        check("产物路径带 data/ 前缀（API 口径）", outs[0]["path"].startswith("data/"), outs[0]["path"])
        check("产物尺寸 = 1280x960",
              outs[0].get("width") == 1280 and outs[0].get("height") == 960)
        check("产物 sha256 为 64 位十六进制",
              len(str(outs[0].get("sha256") or "")) == 64)
        check("产物 size_bytes 有值", outs[0].get("size_bytes") is not None)

    print("\n[6] 任务日志")
    st, logs, _ = http("GET", f"/tasks/{final['id']}/logs")
    check("日志端点 200", st == 200, f"HTTP {st}")
    check("正常任务日志非空", isinstance(logs, list) and len(logs) >= 1,
          f"{len(logs) if isinstance(logs, list) else logs} 条")
    check("日志条目含 level/timestamp/message",
          bool(logs) and all(k in logs[0] for k in ("level", "timestamp", "message")))

    print("\n[7] 文件内容 original / thumb / result")
    sizes = {}
    for variant in ("original", "thumb", "result"):
        st, body, hdr = http("GET", f"/files/{file_id}/content?variant={variant}", raw=True)
        ctype = hdr.get("content-type", "")
        sizes[variant] = len(body)
        check(f"variant={variant} → 200 + image/*",
              st == 200 and len(body) > 0 and ctype.startswith("image/"),
              f"HTTP {st} {ctype} {len(body)}B")
    check("result 体积 ≫ original（确实完成超分）",
          sizes.get("result", 0) > sizes.get("original", 0) * 10, str(sizes))

    print("\n[8] 异常链路（受控 4xx + 服务仍可用）")
    st, err, _ = http("GET", "/files/file_doesnotexist/content?variant=original")
    check("不存在 file_id → 404", st == 404, f"HTTP {st}")
    check("统一错误体三要素齐备（code/message/suggestion）",
          isinstance(err, dict) and all(err.get("error", {}).get(k) for k in ("code", "message", "suggestion")),
          json.dumps(err, ensure_ascii=False)[:140] if isinstance(err, dict) else str(err))
    check("不存在 task_id → 4xx", 400 <= http("GET", "/tasks/tsk_doesnotexist")[0] < 500)
    st, body, _ = http("POST", "/tasks", json.dumps(
        {"type": "upscale", "file_id": file_id, "params": {"scale": 4}}).encode(),
        {"Content-Type": "application/json"})
    check("缺必填参数 → 400 VALIDATION_ERROR",
          st == 400 and body.get("error", {}).get("code") == "VALIDATION_ERROR", f"HTTP {st}")
    check("异常后服务仍可用", http("GET", "/system/capabilities")[0] == 200)

    print("\n[9] 标定消费口径（命中记录 → 取值必须等于记录显式给出的推荐值）")
    if cal is None:
        skip("标定消费口径", "本次环境没有「已标定且可用」的模型")
    else:
        rec = next(r for r in recs if f"mdl_{r['model_id']}" == cal["id"])
        expected_tile = (rec.get("tile_curve") or {}).get("recommended_tile")
        final2, _f2 = run_stream(file_id, cal["id"])
        res2 = final2.get("resolved") or {}
        print(f"    已标定模型 {cal['id']}：source={res2.get('source')} tile={res2.get('tile')} "
              f"using_fallback={res2.get('using_fallback')}（记录推荐 {expected_tile}）")
        check("已标定模型任务完成", final2["status"] == "completed", final2["status"])
        if res2.get("source") == "calibration":
            check("命中标定 → using_fallback=False", res2.get("using_fallback") is False,
                  str(res2.get("using_fallback")))
            check("命中标定 → tile 等于记录推荐的 recommended_tile",
                  res2.get("tile") == expected_tile, f"{res2.get('tile')} vs {expected_tile}")
        else:
            skip("标定消费取值比对",
                 f"该记录指纹与本机不匹配（source={res2.get('source')}）—— 按设计回落保底档")

    # ---- 10. 经代理 SSE（浏览器侧链路）----
    if ":5173" in BASE:
        print("\n[10] 经 Vite 代理的 SSE 必须为流式")
        _f, frames3 = run_stream(file_id, probe_model)
        ts = [t for e, _d, t in frames3 if e != "__error__"]
        span = (max(ts) - min(ts)) if ts else 0.0
        check("经代理收到完整帧序",
              [e for e, _d, _t in frames3][:1] == ["snapshot"] and [e for e, _d, _t in frames3][-1:] == ["done"])
        check("帧分散到达（非一次性缓冲）", span > 0.05, f"跨度 {span:.3f}s")

    print("\n== 结果 ==")
    print(f"{PASS} 通过 / {len(FAIL)} 失败 / {len(SKIP)} 跳过")
    for s in SKIP:
        print("  [SKIP]", s)
    if FAIL:
        for f in FAIL:
            print("  [FAIL]", f)
        sys.exit(1)
    print("全部通过 ✅")


if __name__ == "__main__":
    main()
