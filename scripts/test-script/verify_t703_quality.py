"""T-703 主链路系统联调（单图超分端到端 · 画质与接缝验收）。

⚠️ 这是**第一个回答「画得好不好」的脚本**。

`verify_t702_e2e.py` 回答的是「能出图 / 多快 / 链路通 / 数据对」；
`verify_t706_testcases.py`（M7）才做逐条验收标准。本脚本填的是长期挂账的那块：
**真实照片的人眼画质复核 + F-11「无可见接缝」**（PRD §2.5 / §2.7）。

用法（**需先启动后端**）：
    .venvs/sr-app/Scripts/python.exe scripts/test-script/verify_t703_quality.py
    # 只看矩阵不看大图（省时）：
    .venvs/sr-app/Scripts/python.exe scripts/test-script/verify_t703_quality.py --fast

覆盖：
  1  内置模型矩阵：逐个模型走真实链路，记录 resolved(tile/overlap/feather)/耗时/产物
  2  F-01 客观验收：输出尺寸 = 源 ×4（**误差 0**）
  3  F-01 色彩验收：无通道错乱（R↔B 互换反证法，比"看着正常"硬）
  4  F-11 接缝验收（客观·双 tile 一致性）：同图不同 tile 各跑一次，
     差异**不得沿任一方块网格呈周期性结构**（比"看图找缝"可复现）
  5  F-11 接缝验收（客观·梯度突变）：沿已知 tile 边界的不连续性 vs 块内基线
  6  F-11 接缝验收（人眼）：导出边界裁剪条 + 并排对比图，供人眼复核
  7  官方参考交叉校验：与 Real-ESRGAN **官方 ncnn 可执行文件**的产物比对
  8  不可用模型的可观测性：OpenVINO IR 在应用环境应给**可读**的 runtime_missing

设计纪律：
  - **不硬编码本机实测值**：tile/overlap/feather 一律取自任务返回的 `resolved`。
  - 判据优先用**相对/自指**形式（互换反证、双跑一致性、边界 vs 块内），
    少用绝对阈值 —— 绝对阈值会随图像内容漂移，容易变成"调参调到绿"。
"""
import json
import math
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / ".workbuddy" / "verify" / "t703" / "fixtures"
OUT = ROOT / ".workbuddy" / "verify" / "t703" / "out"
# 官方 ncnn 可执行文件的历史产物（T-210 留下的同源参考实现输出）
REF_EXE = ROOT / ".workbuddy" / "verify" / "t210" / "exe_out_x4.png"
REF_EXE_INPUT = ROOT / ".workbuddy" / "verify" / "t210" / "ncnn_pkg" / "input.jpg"

BASE = "http://127.0.0.1:8000/api"
FAIL, PASS, SKIP = [], 0, []
FAST = "--fast" in sys.argv


def check(label, ok, extra=""):
    global PASS
    if ok:
        PASS += 1
    else:
        FAIL.append(label)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {extra}" if extra else ""))


def skip(label, why):
    SKIP.append(f"{label}（{why}）")
    print(f"  [SKIP] {label} — {why}")


def note(text):
    print(f"         {text}")


def http(method, path, data=None, headers=None, raw=False, timeout=900):
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return r.status, (body if raw else json.loads(body.decode() or "null"))
    except urllib.error.HTTPError as e:
        raw_body = e.read()
        try:
            return e.code, json.loads(raw_body.decode() or "null")
        except Exception:
            return e.code, raw_body


# ---------------------------------------------------------------- 夹具与工具

def fixture_paths():
    """返回 [(标签, 路径, 用途)]；缺失的夹具跳过（数据集不入库，由 fetch 命令重建）。"""
    items = []
    for label, p, kind in [
        ("ncnn-input(动漫220²)", REF_EXE_INPUT, "ref"),
        ("ncnn-input2(海滩写实256²)", ROOT / ".workbuddy" / "verify" / "t210" / "ncnn_pkg" / "input2.jpg", "photo"),
        ("photo-00003(写实512x256)", FIX / "photo-00003.png", "photo"),
        ("anime-OST_009(动漫448x640)", FIX / "anime-OST_009.png", "anime"),
        ("fronalpstock-1600(写实1920x863)", FIX / "photo-fronalpstock-1600.jpg", "large"),
    ]:
        if p.is_file():
            items.append((label, p, kind))
    return items


def upload(path, name=None):
    name = name or path.name
    ctype = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    boundary = "----WebSR" + uuid.uuid4().hex
    b = boundary.encode()
    body = b"".join([
        b"--", b, b"\r\n",
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode(),
        f"Content-Type: {ctype}\r\n\r\n".encode(), path.read_bytes(), b"\r\n", b"--", b, b"--\r\n",
    ])
    return http("POST", "/files/upload", body,
                {"Content-Type": f"multipart/form-data; boundary={boundary}"})


def create_task(file_id, model_id, tile=None):
    return http("POST", "/tasks", json.dumps({
        "type": "upscale", "file_id": file_id,
        "params": {"scale": 4, "model_id": model_id, "tile": tile,
                   "precision": None, "backend": None, "auto": tile is None}}).encode(),
        {"Content-Type": "application/json"})


def run_once(path, model_id, tile=None):
    """上传 → 建任务 → 轮询终态。返回 (终态任务, 上传响应)。"""
    st, up = upload(path)
    if st not in (200, 201):
        return {"__upload_error__": f"HTTP {st} {up}"}, up
    st, task = create_task(up["file_id"], model_id, tile)
    if st not in (200, 201):
        return {"__create_error__": f"HTTP {st} {task}"}, up
    t0, final = time.monotonic(), task
    deadline = time.time() + 900
    while time.time() < deadline:
        st, t = http("GET", f"/tasks/{task['id']}", timeout=30)
        if st == 200:
            final = t
            if t["status"] in ("completed", "failed", "canceled", "interrupted"):
                break
        time.sleep(0.4)
    final["__wall_s__"] = round(time.monotonic() - t0, 2)
    return final, up


def output_path(task):
    for a in (task.get("artifacts") or []):
        if a.get("kind") == "output":
            p = ROOT / a["path"]
            if p.is_file():
                return p
    return None


# ---------------------------------------------------------------- 客观判据

def load(path):
    import numpy as np
    from PIL import Image
    return np.asarray(Image.open(path).convert("RGB")).astype(np.float64)


def color_mapping_ok(src, dst):
    """R↔B 互换反证：把输出按 ×scale 面积平均回源尺寸，比较恒等映射与互换映射的误差。

    若管线把 BGR 当 RGB（或反之），恒等映射误差会**显著大于**互换映射。
    返回 (identity_err, swap_err)。判据是 identity_err < swap_err。
    """
    import numpy as np
    h, w = src.shape[:2]
    dh, dw = dst.shape[:2]
    sy, sx = dh // h, dw // w
    if sy < 1 or sx < 1:
        return None, None
    box = dst[: h * sy, : w * sx].reshape(h, sy, w, sx, 3).mean(axis=(1, 3))
    identity = float(np.abs(box - src).mean())
    swapped = float(np.abs(box[..., ::-1] - src).mean())
    return identity, swapped


def grad_profile(arr):
    """逐列平均梯度（跨通道），用于找接缝。"""
    import numpy as np
    g = np.abs(np.diff(arr, axis=1)).mean(axis=2)   # (H, W-1)
    return g.mean(axis=0)                            # (W-1,)


def seam_excess(arr, tile, overlap, scale):
    """沿已知 tile 边界的不连续性 vs 块内基线。

    返回 (boundary_mean, interior_mean, ratio, n_boundaries)。
    拼接正确时 ratio 应接近 1（边界处不应比块内更"陡"）。
    """
    import numpy as np
    stride_src = max(tile - overlap, 1)
    stride_out = stride_src * scale
    prof = grad_profile(arr)
    W = prof.shape[0]
    bounds = [k * stride_out for k in range(1, 200) if k * stride_out < W - 4]
    if not bounds:
        return None
    win = max(int(overlap * scale / 2), 4)
    bvals, ivals = [], []
    for b in bounds:
        lo, hi = max(0, b - win), min(W, b + win)
        if hi > lo:
            bvals.append(float(prof[lo:hi].mean()))
        # 块内对照：取该 block 的中间位置，同宽度
        mid = b + stride_out // 2
        lo2, hi2 = min(W - 1, mid - win), min(W, mid + win)
        if hi2 > lo2:
            ivals.append(float(prof[lo2:hi2].mean()))
    if not bvals or not ivals:
        return None
    bm, im = float(np.mean(bvals)), float(np.mean(ivals))
    return bm, im, (bm / im if im > 1e-9 else float("inf")), len(bounds)


def grid_structure(diff, stride_out, tol=6):
    """差异图是否沿给定网格呈周期性结构。

    做法：把 |diff| 按列聚合成 profile，比较「网格列 ±tol」与「其余列」的均值。
    无周期性伪影时，两者应相当（ratio ≈ 1）。
    """
    import numpy as np
    prof = diff.mean(axis=(0, 2)) if diff.ndim == 3 else diff.mean(axis=0)
    W = prof.shape[0]
    if stride_out < 8 or W < stride_out * 2:
        return None
    mask = np.zeros(W, dtype=bool)
    for k in range(1, 200):
        c = k * stride_out
        if c >= W:
            break
        mask[max(0, c - tol):min(W, c + tol + 1)] = True
    if mask.sum() < 8 or (~mask).sum() < 8:
        return None
    on, off = float(prof[mask].mean()), float(prof[~mask].mean())
    return on, off, (on / off if off > 1e-9 else float("inf"))


def psnr(a, b):
    import numpy as np
    if a.shape != b.shape:
        return None
    mse = float(((a - b) ** 2).mean())
    return None if mse <= 0 else 10 * math.log10((255.0 ** 2) / mse)


# ---------------------------------------------------------------- 人眼复核素材

def save_compare(src_path, dst_path, out_path, title=""):
    """左：双三次放大的输入（"之前"）｜右：模型输出（"之后"）。缩到可浏览尺寸。"""
    import numpy as np
    from PIL import Image
    src = Image.open(src_path).convert("RGB")
    dst = Image.open(dst_path).convert("RGB")
    before = src.resize(dst.size, Image.BICUBIC)
    gap, pad = 8, 0
    W = dst.width * 2 + gap
    H = dst.height
    canvas = Image.new("RGB", (W, H), (128, 128, 128))
    canvas.paste(before, (0, 0))
    canvas.paste(dst, (dst.width + gap, 0))
    maxw = 1900
    if canvas.width > maxw:
        canvas = canvas.resize((maxw, int(canvas.height * maxw / canvas.width)), Image.LANCZOS)
    canvas.save(out_path)
    return out_path


def save_zoom(src_path, dst_path, out_path, box=None):
    """100% 像素级并排裁剪（判断细节真假、是否糊/是否过度锐化）。"""
    from PIL import Image
    src = Image.open(src_path).convert("RGB")
    dst = Image.open(dst_path).convert("RGB")
    if box is None:  # 默认取中心 1/4 区域
        w, h = dst.size
        box = (w // 2 - w // 8, h // 2 - h // 8, w // 2 + w // 8, h // 2 + h // 8)
    crop_d = dst.crop(box)
    sx = box[0] // 4, box[1] // 4, max(box[2] // 4, box[0] // 4 + 1), max(box[3] // 4, box[1] // 4 + 1)
    crop_s = src.crop(sx).resize(crop_d.size, Image.NEAREST)  # 最近邻 = "未超分"的真实观感
    gap = 6
    canvas = Image.new("RGB", (crop_d.width * 2 + gap, crop_d.height), (128, 128, 128))
    canvas.paste(crop_s, (0, 0))
    canvas.paste(crop_d, (crop_d.width + gap, 0))
    if canvas.width > 1900:
        canvas = canvas.resize((1900, int(canvas.height * 1900 / canvas.width)), Image.LANCZOS)
    canvas.save(out_path)
    return out_path


def save_seam_strips(dst_path, tile, overlap, scale, out_dir, prefix, k=3):
    """沿 tile 边界切条（100% 缩放），人眼直接找缝。"""
    from PIL import Image
    import numpy as np
    dst = Image.open(dst_path).convert("RGB")
    arr = np.asarray(dst).astype(np.float64)
    stride_out = (tile - overlap) * scale
    W, H = dst.size
    bounds = [b for b in (i * stride_out for i in range(1, 200)) if b < W - 4]
    if not bounds:
        return []
    # 选「梯度最大」的若干边界（内容越丰富越容易暴露缝）
    prof = grad_profile(arr)
    bounds.sort(key=lambda b: -float(prof[max(0, b - 8):b + 8].mean()))
    made = []
    for i, b in enumerate(bounds[:k]):
        half = min(220, max(90, stride_out // 3))
        x0, x1 = max(0, b - half), min(W, b + half)
        y0 = max(0, H // 2 - 130)
        y1 = min(H, y0 + 260)
        crop = dst.crop((x0, y0, x1, y1)).resize(((x1 - x0) * 2, (y1 - y0) * 2), Image.NEAREST)
        p = out_dir / f"{prefix}_seam{i}_x{b}.png"
        crop.save(p)
        made.append((p, b))
    return made


# ---------------------------------------------------------------- 主流程

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"== T-703 主链路系统联调 · 画质与接缝验收（base={BASE}）==")

    st, _ = http("GET", "/system/capabilities", timeout=10)
    if st != 200:
        print(f"\n❌ 后端不可达（HTTP {st}）。请先启动后端：")
        print("   cd server && ../.venvs/sr-app/Scripts/python.exe -m uvicorn app.main:app --port 8000")
        sys.exit(2)

    models = http("GET", "/models")[1] or []
    by_id = {m["id"]: m for m in models}
    avail = [m for m in models if m.get("available")]
    note(f"模型库 {len(models)} 个（available={len(avail)}）："
         + ", ".join(f"{m['id']}={m['name']}" for m in models))

    fixtures = fixture_paths()
    print(f"        夹具 {len(fixtures)} 张：" + ", ".join(f"{l}" for l, _p, _k in fixtures))

    results = []   # (label, model_id, tile, final, out_path)

    # ================= 1. 模型矩阵（画质主评） =================
    print("\n[1] 内置模型矩阵 · 真实链路逐模型")
    photo = next((p for l, p, k in fixtures if k == "photo" and "512" in l), None)
    matrix = [
        ("photo-00003", photo, "mdl_1", None),   # 写实主力
        ("photo-00003", photo, "mdl_2", None),   # fp16 档
        ("photo-00003", photo, "mdl_4", None),   # 动漫模型跑写实图（对照）
        ("photo-00003", photo, "mdl_5", None),   # 轻量档
    ]
    anime = next((p for l, p, k in fixtures if k == "anime" and "OST" in l), None)
    if anime:
        matrix.append(("anime-OST_009", anime, "mdl_4", None))
    beach = next((p for l, p, k in fixtures if "input2" in l), None)
    if beach:
        matrix.append(("ncnn-input2", beach, "mdl_1", None))
    # 官方 ncnn 可执行文件的历史产物就出自这张图（用于第 7 段交叉校验）
    if REF_EXE_INPUT.is_file():
        matrix.append(("ncnn-input", REF_EXE_INPUT, "mdl_1", None))

    for label, path, mid, tile in matrix:
        if mid not in by_id:
            skip(f"{label} × {mid}", "模型未登记")
            continue
        final, _up = run_once(path, mid, tile)
        if "__upload_error__" in final or "__create_error__" in final:
            check(f"{label} × {mid} 建任务", False, str(final)[:120])
            continue
        ok = final.get("status") == "completed"
        res = final.get("resolved") or {}
        ex = res.get("execution") or {}
        check(f"{label} × {mid} 完成", ok,
              f"status={final.get('status')} err={final.get('error')}")
        if not ok:
            continue
        op = output_path(final)
        results.append((label, mid, tile, final, op))
        note(f"{mid}: {final.get('output_width')}x{final.get('output_height')} "
             f"tile={res.get('tile')} overlap={res.get('overlap')} feather={res.get('feather_px')} "
             f"source={res.get('source')} 块数={ex.get('tiles')} "
             f"耗时={ex.get('elapsed_ms')}ms 墙钟={final.get('__wall_s__')}s")

    # ================= 2/3. F-01 尺寸与色彩 =================
    print("\n[2] F-01 客观验收：尺寸 = 源 × 4（误差 0）+ 色彩无通道错乱")
    for label, mid, tile, final, op in results:
        src_p = next((p for l, p, _k in fixtures if l.split("(")[0] == label), None)
        if src_p is None:
            skip(f"{label} × {mid} F-01", "找不到源夹具")
            continue
        src = load(src_p)
        h, w = src.shape[:2]
        ow, oh = final.get("output_width"), final.get("output_height")
        # 产品按 align 向上取整到 8 的倍数，故允许 0..7 的极小幅对齐余量，但必须 >= 精确值
        ok_size = (oh == h * 4 and ow == w * 4)
        check(f"{label} × {mid} 尺寸精确 = {w*4}x{h*4}", ok_size, f"实际 {ow}x{oh}")
        if op is None:
            skip(f"{label} × {mid} 色彩", "产物文件不可读")
            continue
        ident, swap = color_mapping_ok(src, load(op))
        if ident is None:
            skip(f"{label} × {mid} 色彩", "尺寸不匹配，跳过")
        else:
            check(f"{label} × {mid} 色彩映射为恒等（非 R↔B 互换）", ident < swap,
                  f"identity={ident:.2f} swap={swap:.2f}")

    # ================= 7. 官方参考交叉校验 =================
    print("\n[7] 官方参考交叉校验（与 Real-ESRGAN 官方 ncnn 可执行文件产物比对）")
    ref_final = next((r for r in results if r[0] == "ncnn-input" and r[1] == "mdl_1"), None)
    if ref_final is None or not REF_EXE.is_file():
        skip("官方交叉校验", "缺 ncnn 参考产物或本次未跑对应组合")
    else:
        op = ref_final[4]
        a, b = load(REF_EXE), load(op)
        if a.shape != b.shape:
            check("官方交叉校验尺寸一致", False, f"{a.shape} vs {b.shape}")
        else:
            p = psnr(a, b)
            mean_abs = float(abs(a - b).mean())
            check("🔎 与官方 ncnn 实现的输出结构一致（PSNR ≥ 28 dB）",
                  p is not None and p >= 28.0,
                  f"PSNR={p:.2f} dB，平均绝对差={mean_abs:.2f}")
            check("🔎 与官方实现的通道均值一致（无系统性色彩偏移）",
                  float(abs(a.reshape(-1, 3).mean(0) - b.reshape(-1, 3).mean(0)).max()) < 3.0,
                  f"最大通道均值差={float(abs(a.reshape(-1,3).mean(0)-b.reshape(-1,3).mean(0)).max()):.2f}")

    # ================= 8. OpenVINO IR 在应用环境的可观测性 =================
    print("\n[8] 缺运行时的模型：列表门控 + 任务错误契约（T-703 修复回归）")
    if photo is None:
        skip("缺运行时模型的可观测性", "缺夹具")
    elif "mdl_3" not in by_id:
        skip("缺运行时模型的可观测性", "mdl_3 未登记")
    else:
        import importlib.util as _ilu

        ov = _ilu.find_spec("openvino") is not None
        m3 = by_id["mdl_3"]
        check("① 列表可用性反映『本机能否跑』（与 openvino 是否安装一致）",
              m3.get("available") is ov,
              f"available={m3.get('available')} · openvino={'已装' if ov else '未装'}")
        if not ov:
            check("① 置灰原因可照做（含 openvino 与安装动作）",
                  "openvino" in (m3.get("unavailable_reason") or "")
                  and "pip install" in (m3.get("unavailable_reason") or ""),
                  str(m3.get("unavailable_reason"))[:110])

        # 即便列表已置灰，接口层仍不得让"必然失败"变得不可诊断（纵深防御）
        final3, _ = run_once(photo, "mdl_3")
        err = final3.get("error") if isinstance(final3.get("error"), dict) else {}
        print(f"        status={final3.get('status')} code={err.get('code')} "
              f"detail.code={(err.get('detail') or {}).get('code')}")
        check("② 任务显式失败而非崩溃", final3.get("status") == "failed", str(final3.get("status")))
        check("② 契约 §2.3 #8：错误码为 MODEL_INCOMPATIBLE",
              err.get("code") == "MODEL_INCOMPATIBLE", str(err.get("code")))
        check("② 具体原因出现在 detail.code（契约指定位置）",
              (err.get("detail") or {}).get("code") == "runtime_missing",
              str((err.get("detail") or {}).get("code")))
        check("② message 可读且指向缺失的运行时",
              "openvino" in (err.get("message") or ""),
              str(err.get("message"))[:110])
        check("② suggestion 给出可照做的修复动作（不是『导出诊断 JSON』）",
              "pip install" in (err.get("suggestion") or ""),
              str(err.get("suggestion"))[:110])
        check("服务在失败后仍可用", http("GET", "/system/capabilities", timeout=10)[0] == 200)

    # ================= 4/5/6. 接缝验收（大图 · 多块） =================
    print("\n[4-6] F-11 接缝验收（大图强制多块）")
    large = next((p for l, p, k in fixtures if k == "large"), None)
    if FAST:
        skip("接缝验收", "--fast 模式跳过")
    elif large is None:
        skip("接缝验收", "缺大图夹具（fronalpstock-1600，见 fixtures/README）")
    else:
        f256, _ = run_once(large, "mdl_1", 256)
        f128, _ = run_once(large, "mdl_1", 128)
        for tag, f in (("tile=256", f256), ("tile=128", f128)):
            res = f.get("resolved") or {}
            ex = res.get("execution") or {}
            check(f"大图 {tag} 完成", f.get("status") == "completed",
                  f"status={f.get('status')} err={f.get('error')}")
            note(f"{tag}: 块数={ex.get('tiles')} tile={res.get('tile')} "
                 f"overlap={res.get('overlap')} feather={res.get('feather_px')} "
                 f"耗时={ex.get('elapsed_ms')}ms 墙钟={f.get('__wall_s__')}s")

        if f256.get("status") == "completed" and f128.get("status") == "completed":
            p256, p128 = output_path(f256), output_path(f128)
            import numpy as np
            from PIL import Image
            a256, a128 = load(p256), load(p128)
            check("两次运行输出尺寸一致", a256.shape == a128.shape, f"{a256.shape} vs {a128.shape}")
            if a256.shape == a128.shape:
                diff = np.abs(a256 - a128)
                note(f"双跑差异：均值={diff.mean():.2f} 峰值={diff.max():.0f} "
                     f"（不同 tile 的真实数值差异，非缺陷）")
                for tag, f, s in (("tile=256 网格", f256, 256), ("tile=128 网格", f128, 128)):
                    res = f.get("resolved") or {}
                    tile = res.get("tile") or s
                    ov = res.get("overlap") or 0
                    g = grid_structure(diff, (tile - ov) * 4)
                    if g is None:
                        skip(f"周期性伪影检测（{tag}）", "网格过密/尺寸不足")
                    else:
                        on, off, ratio = g
                        check(f"🔴 差异不沿 {tag} 呈周期性结构（ratio < 1.25）", ratio < 1.25,
                              f"网格列均值={on:.2f} 其余={off:.2f} ratio={ratio:.3f}")
                # 梯度突变：边界 vs 块内
                for tag, f in (("tile=256", f256), ("tile=128", f128)):
                    res = f.get("resolved") or {}
                    tile, ov = res.get("tile"), res.get("overlap")
                    if not tile or ov is None:
                        continue
                    se = seam_excess(load(output_path(f)), tile, ov, 4)
                    if se is None:
                        continue
                    bm, im, ratio, n = se
                    check(f"🔴 {tag} 块边界无梯度突变（ratio < 1.30，n={n}）", ratio < 1.30,
                          f"边界={bm:.3f} 块内={im:.3f} ratio={ratio:.3f}")

            print("        → 导出人眼复核素材")
            res256 = f256.get("resolved") or {}
            strips = save_seam_strips(p256, res256.get("tile") or 256,
                                      res256.get("overlap") or 0, 4, OUT, "large256")
            note(f"边界裁剪条 {len(strips)} 张 → {OUT}")
            big = Image.open(p256).convert("RGB")
            big = big.resize((1600, int(big.height * 1600 / big.width)), Image.LANCZOS)
            big.save(OUT / "large256_full.png")
            c = save_compare(large, p256, OUT / "large256_compare.png")
            note(f"整图对比 → {c.name}")

    # ================= 6. 画质人眼复核素材 =================
    print("\n[6] 生成人眼复核素材（并排对比 + 100% 放大裁剪）")
    for label, mid, tile, final, op in results:
        if op is None:
            continue
        src_p = next((p for l, p, _k in fixtures if l.split("(")[0] == label), None)
        if src_p is None:
            continue
        c1 = save_compare(src_p, op, OUT / f"cmp_{label}_{mid}.png")
        c2 = save_zoom(src_p, op, OUT / f"zoom_{label}_{mid}.png")
        note(f"{label} × {mid} → {c1.name} / {c2.name}")

    print("\n== 结果 ==")
    print(f"{PASS} 通过 / {len(FAIL)} 失败 / {len(SKIP)} 跳过")
    for s in SKIP:
        print("  [SKIP]", s)
    for f in FAIL:
        print("  [FAIL]", f)
    print(f"\n人眼复核素材目录：{OUT}")
    if FAIL:
        sys.exit(1)
    print("全部通过 ✅")


if __name__ == "__main__":
    main()
