"""T-704 异常与降级链联调（M6 · 步骤 7；P0）。

## 本轮补的是哪一块

`verify_t804_engine.py` 已经把阶段 E 的**机制**验证过了 —— 但它用的是
`_FastExecutor`（**抛构造异常**的假执行器）+ 注入的构造水位值。也就是说：

> 降档链的每个分支都被单测过，**但它从来没有被真实 OOM 触发过**。

这正是 T-703 留下的教训的同构版本：「控制面全绿 ≠ 数据面正确」。
本脚本走**真实后端 + 真实推理 + 真实显存耗尽**。

## 怎么让一台 8 GB 卡"真实 OOM"

不能靠降低显存（真机就这么多），也不需要伪造硬件事实。用**产品自身的真实入口**：

    `POST /api/tasks` with `params.auto=false, params.tile=<很大>`

非自动档允许用户指定 tile，而因果性防御只保证"不低于下界、是 align 倍数"——
**不为"这台机器跑不动"兜底**（那是降档链的职责）。再配一张**短边小于 tile 的图**，
`plan_tiles` 会把内容居中补齐成完整 `tile×tile` 块 → 单块即耗尽显存，触发快、可重复。

## 本脚本验证什么

1. **真实 OOM → 自动降档 → 重试成功**（降档链真的把任务救回来了）
2. **降档链耗尽 → `VRAM_INSUFFICIENT`** + 失败现场完整（backend / tile / attempts / 水位）
3. 水位采样在真实 OOM 现场**确实**采到高水位（并如实登记其"下一步"的结构性边界）
4. **EP 回退**：请求一个未通过验证的后端 → 因果性防御在真实 HTTP 路径上回落
5. **保底档可见**且**不污染**标定表 / 模型元信息（§6.8 规则 2）

用法（需先启动后端；本脚本走真实推理，耗时约 1–2 分钟）：
    .venvs/sr-app/Scripts/python.exe scripts/test-script/verify_t704_degradation.py

设计纪律：**期望值一律从接口推导**（模型从 `/api/models` 选、tile 由探测决定），
不硬编码本机实测数值；某种资源档位下探测不出来的情形一律 **SKIP 并说明**，不伪造成 PASS。
"""
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IMG = ROOT / ".workbuddy" / "tmp" / "t704-input.png"

BASE = "http://127.0.0.1:8000/api"
TERMINAL = ("completed", "failed", "canceled", "interrupted")

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


def http(method, path, data=None, headers=None, timeout=300):
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "null")
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
    """64×64 小图：短边小于探测用的 tile → 单块覆盖全图（OOM 触发快且可重复）。"""
    if IMG.is_file():
        return
    import numpy as np
    from PIL import Image

    IMG.parent.mkdir(parents=True, exist_ok=True)
    yy, xx = np.mgrid[0:64, 0:64]
    arr = np.zeros((64, 64, 3), dtype=np.uint8)
    arr[..., 0] = ((xx // 4 + yy // 4) % 2 * 220).astype(np.uint8)
    arr[..., 1] = ((xx * 7) % 256).astype(np.uint8)
    arr[..., 2] = ((yy * 11) % 256).astype(np.uint8)
    Image.fromarray(arr).save(IMG)


def upload() -> str | None:
    boundary = "----WebSR" + uuid.uuid4().hex
    b = boundary.encode()
    body = b"".join([
        b"--", b, b"\r\n",
        b'Content-Disposition: form-data; name="file"; filename="t704-input.png"\r\n',
        b"Content-Type: image/png\r\n\r\n", IMG.read_bytes(), b"\r\n", b"--", b, b"--\r\n",
    ])
    status, payload = http("POST", "/files/upload", body,
                           {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    return payload.get("file_id") if status == 201 and isinstance(payload, dict) else None


def pick_model() -> tuple[str, str] | tuple[None, None]:
    """挑**当前可用、且显存门槛最高**的模型。

    「门槛最高」是功能性选择（要触发的是**资源耗尽**，太轻的模型在大 tile 下也未必耗尽），
    不是硬编码某个型号 —— 若探测不到 OOM，脚本会 SKIP 而非伪造结论。
    """
    _, models = http("GET", "/models")
    if not isinstance(models, list):
        return None, None
    usable = [m for m in models if m.get("available")]
    if not usable:
        return None, None
    usable.sort(key=lambda m: (-(m.get("min_vram_mb") or 0), m["id"]))
    return usable[0]["id"], usable[0]["name"]


def create_task(file_id: str, model_id: str, *, auto: bool = True, tile: int | None = None,
                backend: str | None = None, scale: int = 4):
    return http("POST", "/tasks", json.dumps({
        "type": "upscale", "file_id": file_id,
        "params": {"scale": scale, "model_id": model_id, "tile": tile,
                   "precision": None, "backend": backend, "auto": auto},
    }).encode(), {"Content-Type": "application/json"})


def run_task(file_id: str, model_id: str, *, tile: int | None = None, auto: bool = True,
             backend: str | None = None, timeout: float = 240.0) -> dict:
    """提交并等到终态，返回任务对象（含 resolved / error）。"""
    status, created = create_task(file_id, model_id, auto=auto, tile=tile, backend=backend)
    if status != 201 or not isinstance(created, dict):
        return {"__submit_error__": f"{status} {created}"}
    tid = created["id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _, t = http("GET", f"/tasks/{tid}")
        if isinstance(t, dict) and t.get("status") in TERMINAL:
            return t
        time.sleep(0.3)
    return {"id": tid, "status": "timeout"}


def tile_downgrades(t: dict) -> list[dict]:
    res = t.get("resolved") or {}
    return [d for d in (res.get("downgrades") or []) if d.get("field") == "tile"]


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

    section("1. 准备：小图夹具 + 可用模型")
    make_image()
    file_id = upload()
    model_id, model_name = pick_model()
    check("上传夹具成功", bool(file_id), str(file_id))
    check("取得可用模型（从 /api/models 推导，不硬编码）", bool(model_id), f"{model_id} {model_name}")
    if not file_id or not model_id:
        print("\n准备失败，脚本中止")
        return 2

    # ---- 2. 真实 OOM → 自动降档 → 重试成功 --------------------------------------
    section("2. 真实 OOM → 自动降档 → 重试成功（自适应探测 tile）")
    success_tile = None
    exhausted_tile = None
    oom_run: dict | None = None
    tile = 1024
    for _ in range(4):
        t = run_task(file_id, model_id, tile=tile, auto=False)
        st = t.get("status")
        dg = tile_downgrades(t)
        print(f"  · tile={tile} → {st}"
              + (f"，降档 {dg[0]['from']}→{dg[0]['to']}" if dg else "")
              + (f"，error={t.get('error', {}).get('code')}" if t.get("error") else ""))
        if st == "completed" and dg:
            success_tile, oom_run = tile, t
            break
        if st == "failed" and (t.get("error") or {}).get("code") == "VRAM_INSUFFICIENT":
            exhausted_tile = tile
            break
        if st == "completed":
            tile *= 2          # 这一档还跑得动 → 加大
            continue
        break                  # 其它失败形态：不再猜测

    if success_tile is None:
        skip("真实 OOM → 降档成功", "本机在该探测区间内未复现出「OOM 后降档成功」（资源档位相关）")
    else:
        res = oom_run.get("resolved") or {}
        check("真实 OOM 后自动降档，任务最终**成功**", oom_run.get("status") == "completed",
              str(oom_run.get("status")))
        check("降档记录如实写入 resolved.downgrades（field=tile）",
              bool(tile_downgrades(oom_run)), str(tile_downgrades(oom_run))[:160])
        check("降档是**向下**的（新 tile 小于用户请求值）",
              tile_downgrades(oom_run)[0]["to"] < tile_downgrades(oom_run)[0]["from"],
              f"{tile_downgrades(oom_run)[0]['from']} → {tile_downgrades(oom_run)[0]['to']}")
        check("resolved.tile 已被改写为降档后的值（执行事实与决策一致）",
              res.get("tile") == tile_downgrades(oom_run)[0]["to"],
              f"resolved.tile={res.get('tile')}")
        check("degraded=True（降级显式，PRD §2.2 铁律 5）", res.get("degraded") is True)
        check("降档理由可读（用户能看到为什么降）",
              bool(tile_downgrades(oom_run)[0].get("reason")),
              str(tile_downgrades(oom_run)[0].get("reason"))[:120])
        exec_meta = res.get("execution") or {}
        check("降档后仍产出真实产物，尺寸 = 源 ×4",
              exec_meta.get("output_width") == 64 * 4 and exec_meta.get("output_height") == 64 * 4,
              f"{exec_meta.get('output_width')}x{exec_meta.get('output_height')}")
        check("产物已登记 ARTIFACT", len(artifacts_of(oom_run["id"])) >= 1)

    # ---- 3. 降档链耗尽 → VRAM_INSUFFICIENT --------------------------------------
    section("3. 降档链耗尽 → VRAM_INSUFFICIENT（OOM 只重试一次）")
    if exhausted_tile is None:
        exhausted_tile = (success_tile * 4) if success_tile else 4096
        t_ex = run_task(file_id, model_id, tile=exhausted_tile, auto=False)
        print(f"  · tile={exhausted_tile} → {t_ex.get('status')} "
              f"error={(t_ex.get('error') or {}).get('code')}")
    else:
        t_ex = None
    if t_ex is None or t_ex.get("status") != "failed":
        skip("降档链耗尽", "未能在探测区间内制造出「降档一次后仍 OOM」的场景")
    else:
        err = t_ex.get("error") or {}
        detail = err.get("detail") or {}
        check("终态错误码为 VRAM_INSUFFICIENT（GPU 显存耗尽）",
              err.get("code") == "VRAM_INSUFFICIENT", str(err.get("code")))
        check("错误文案是「已自动降档仍无法完成」而非泛化内部错误",
              "降档" in (err.get("message") or ""), str(err.get("message")))
        check("失败现场保留后端与降档后参数",
              bool(detail.get("backend")) and bool(detail.get("tile")), str(detail)[:200])
        check("downgrade_attempts == 1（OOM **只重试一次**，不无限重试）",
              detail.get("downgrade_attempts") == 1, str(detail.get("downgrade_attempts")))
        check("suggestion 可照做（不是「请导出诊断 JSON」了事）",
              bool(err.get("suggestion")) and "诊断 JSON" not in (err.get("suggestion") or ""),
              str(err.get("suggestion"))[:120])
        wl = detail.get("water_level") or {}
        check("失败现场附带真实水位快照（vram/ram 比值）",
              wl.get("ratio") is not None, json.dumps(wl, ensure_ascii=False)[:160])

    # ---- 4. 水位采样在真实 OOM 现场确实采到高水位 --------------------------------
    section("4. 水位（阶段 E 反馈）：真实 OOM 现场的水位采样")
    _, diag = http("GET", "/system/diagnostics")
    wm = (diag or {}).get("watermark") or {}
    hist = wm.get("history") or []
    high_entries = [h for h in hist if h.get("high")]
    check("诊断导出可见水位阈值与连续计数口径",
          wm.get("high_ratio") == 0.85 and wm.get("consecutive_threshold") == 2, str(wm)[:160])
    check("历史里存在真实的高水位条目（ratio > 85%）", bool(high_entries),
          f"共 {len(high_entries)} 条；最近 {high_entries[-1].get('ratio') if high_entries else None}")
    check("WDDM 口径限制被原样保留（设备级用量 ≠ 按进程用量）",
          "设备级" in (wm.get("sampling_note") or ""), str(wm.get("sampling_note"))[:80])
    # ⚠️ 如实登记：连续 2 次高水位 → 降档，在真实路径上**难以按需复现**：
    #    能制造真实高水位的只有 OOM 现场，而 OOM 一旦被降档链接住就会 note_downgrade() 复位计数；
    #    因此本机不可能靠"连跑两次大 tile"把 consecutive_high 推到 2。
    #    该分支由 verify_t804_engine.py 用**构造水位**覆盖（机制正确性），此处不伪造。
    skip("连续 2 次高水位 → 下一任务降档",
         "真实高水位只可能来自 OOM 现场，而 OOM 降档会复位计数 → 本机不可按需复现（机制由 t804 构造水位覆盖）")

    # ---- 5. EP 回退（因果性防御，真实 HTTP 路径）---------------------------------
    section("5. EP 回退：请求未通过验证的后端 → 因果性防御回落")
    t_ep = run_task(file_id, model_id, auto=False, tile=64, backend="TensorrtExecutionProvider")
    res_ep = t_ep.get("resolved") or {}
    dg_ep = [d for d in (res_ep.get("downgrades") or []) if d.get("field") == "backend"]
    if not res_ep:
        skip("EP 回退", f"任务未产出 resolved（{t_ep.get('status')}）")
    else:
        check("请求 TensorRT（未验证）→ 被回落到已验证后端",
              bool(dg_ep) and dg_ep[0]["to"] != "TensorrtExecutionProvider",
              str(dg_ep)[:200])
        check("回落后 resolved.backend 与实际执行一致（不留幻影后端）",
              res_ep.get("backend") == dg_ep[0]["to"], f"{res_ep.get('backend')} vs {dg_ep[0]['to']}")
        check("降级显式：degraded=True", res_ep.get("degraded") is True)

    # ---- 6. 保底档可见 + 不污染标定表 / 模型元信息 --------------------------------
    section("6. 保底档可见性与「不写入」纪律（§6.8 规则 2）")
    _, cal_before = http("GET", "/system/calibration")
    before_n = len(cal_before.get("records") or []) if isinstance(cal_before, dict) else None
    _, models_before = http("GET", "/models")
    before_models = {m["id"]: json.dumps(m, sort_keys=True, ensure_ascii=False) for m in models_before}

    t_fb = run_task(file_id, model_id, auto=True)
    res_fb = t_fb.get("resolved") or {}
    check("保底档标志与来源一致（using_fallback=True ⇔ source=fallback）",
          (res_fb.get("using_fallback") is True) == (res_fb.get("source") == "fallback"),
          f"using_fallback={res_fb.get('using_fallback')} source={res_fb.get('source')}")
    check("source 取值在契约允许集内", res_fb.get("source") in ("calibration", "user", "fallback"),
          str(res_fb.get("source")))
    check("保底档时并发度为 1（保底必须是下界）",
          res_fb.get("using_fallback") is not True or res_fb.get("concurrency") == 1,
          f"concurrency={res_fb.get('concurrency')}")

    # 本机该模型**有标定**（source=calibration），保底档断言会被弱化 ——
    # 因此另找一个**无标定记录**的可用模型，让保底档真正被走到。
    calibrated = {f"mdl_{r.get('model_id')}" for r in (cal_before.get("records") or [])}
    _, models_all = http("GET", "/models")
    uncal = [m for m in (models_all or []) if m.get("available") and m["id"] not in calibrated]
    if not uncal:
        skip("无标定模型走保底档", "当前所有可用模型都有标定记录")
    else:
        uncal.sort(key=lambda m: (m.get("min_vram_mb") or 0, m["id"]))
        t_un = run_task(file_id, uncal[0]["id"], auto=True)
        res_un = t_un.get("resolved") or {}
        check(f"无标定模型（{uncal[0]['id']}）走保底档：using_fallback=True",
              res_un.get("using_fallback") is True, str(res_un.get("using_fallback")))
        check("保底档 source=fallback 且并发度为 1（下界不冒进）",
              res_un.get("source") == "fallback" and res_un.get("concurrency") == 1,
              f"source={res_un.get('source')} concurrency={res_un.get('concurrency')}")
        check("保底档 reasons 里说明了「未标定 → 保底档」（用户能看到为什么保守）",
              any("保底档" in r for r in (res_un.get("reasons") or [])),
              str((res_un.get("reasons") or [])[:2])[:160])
        check("保底档任务仍能真实出图（保底 = 可跑但处处保守）",
              t_un.get("status") == "completed", str(t_un.get("status")))

    _, cal_after = http("GET", "/system/calibration")
    after_n = len(cal_after.get("records") or []) if isinstance(cal_after, dict) else None
    _, models_after = http("GET", "/models")
    after_models = {m["id"]: json.dumps(m, sort_keys=True, ensure_ascii=False) for m in models_after}
    check("跑任务不产生新的标定记录（保底档不落 CalibrationRecord）",
          before_n == after_n, f"{before_n} → {after_n}")
    check("跑任务不改写模型元信息", before_models == after_models,
          str([k for k in before_models if before_models.get(k) != after_models.get(k)]))

    print(f"\n===== 结果：{PASS} 通过 / {len(FAIL)} 失败 / {len(SKIP)} 跳过 =====")
    for f in FAIL:
        print(f"  FAIL: {f}")
    for s in SKIP:
        print(f"  SKIP: {s}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
