"""T-806 模型加载器 + 真实推理编排验证。

覆盖四件事：

1. **格式路由与失败分支**（ADR-003）：五格式的 primary/companion/runtime 判定，
   以及 `.pth`/`.safetensors`（需转换）、缺配套、缺运行时各自抛什么码；
2. **ONNX 输入约束读取**：动态 H/W 与静态 512 两种真实形态、fp16 输入 dtype、
   进程内缓存、"读不到不抛异常"；
3. **真实推理**：ORT 后端 infer、`pipeline` 全链路（预处理 → 分块 → 逐块推理 →
   羽化拼接 → 落盘）、协作式取消、进度阶段与块序；产物尺寸 = 原图 × scale，
   sha256 与文件一致；
4. **接线**：`EngineExecutor` 成为默认执行器并经 HTTP 端到端产出真实 ARTIFACT，
   `TaskOut.output_width/height` 回填，执行时才发现的偏差被显式记入 `downgrades`。

另有两类**源码级检查**：引擎纯度（`engine/` 不 import fastapi / sqlalchemy / pydantic）
与"无设备型号字面量"（ADR-004 原则 2）。

最后以**子进程**在 `.venvs/sr-ov` / `.venvs/sr-ncnn` 里用**产品加载器本身**跑 IR / ncnn
真实推理——应用环境只有 CPU 版 onnxruntime，只在 sr-app 里验证无法证明这两条路径可跑。

运行：`.venvs/sr-app/Scripts/python.exe scripts/test-script/verify_t806_loader.py`
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 临时数据根放在仓库内：可与 data/models/ 做**硬链接**（跨卷会失败，回落复制），
# 避免为了隔离而复制 67 MB 模型；顺带让清理可预期。
_TMP_ROOT = ROOT / ".workbuddy" / "verify" / "t806" / "tmp"
if _TMP_ROOT.exists():
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)
_TMP_ROOT.parent.mkdir(parents=True, exist_ok=True)
DATA = Path(tempfile.mkdtemp(prefix="data_", dir=str(_TMP_ROOT.parent)))

os.environ["APP_DATA_DIR"] = str(DATA)
os.environ["APP_DB_URL"] = f"sqlite:///{(DATA / 'app.db').as_posix()}"

sys.path.insert(0, str(ROOT / "server"))

PASS = FAIL = SKIP = 0


def check(name: str, cond: bool, extra: str = "") -> bool:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")
    return bool(cond)


def skip(name: str, why: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {name} :: {why}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def _link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return
    try:
        os.link(src, dst)  # 同卷硬链接：瞬间完成
    except OSError:
        shutil.copy2(src, dst)


# ---------------------------------------------------------------------------
# 准备：把真实模型"链接"进临时数据根（真实推理必须要真模型）
# ---------------------------------------------------------------------------
REAL_MODELS = ROOT / "data" / "models"
for _rel in (
    "RealESRGAN_x4.onnx",
    "RealESRGAN_x4_fp16.onnx",
    "ir/RealESRGAN_x4_fp16.xml",
    "ir/RealESRGAN_x4_fp16.bin",
):
    if (REAL_MODELS / _rel).is_file():
        _link_or_copy(REAL_MODELS / _rel, DATA / "models" / _rel)

OUT = ROOT / ".workbuddy" / "verify" / "t806"
OUT.mkdir(parents=True, exist_ok=True)
MAIN_ONNX = REAL_MODELS / "RealESRGAN_x4.onnx"
FP16_ONNX = REAL_MODELS / "RealESRGAN_x4_fp16.onnx"
S512_ONNX = REAL_MODELS / "RealESRGAN_x4_s512.onnx"
IR_XML = REAL_MODELS / "ir" / "RealESRGAN_x4_fp16.xml"
IR_BIN = REAL_MODELS / "ir" / "RealESRGAN_x4_fp16.bin"
NCNN_DIR = ROOT / ".workbuddy" / "verify" / "t210" / "ncnn_pkg" / "models"

from app.engine import model_introspect, model_loader, pipeline, runtimes  # noqa: E402
from app.engine.fallback import DEFAULT_ALIGN, overlap_for  # noqa: E402
from app.engine.image_ops import PreprocessSpec  # noqa: E402

# ---------------------------------------------------------------------------
section("1. 可选运行时探测（不 import 重库）")
# ---------------------------------------------------------------------------
desc = runtimes.describe_runtimes()
check("describe_runtimes 结构齐全", {"by_format", "optional", "note"} <= set(desc))
check("describe_runtimes 覆盖 5 种格式",
      set(desc["by_format"]) == {"onnx", "openvino_ir", "ncnn", "pth", "safetensors"})

ort_status = runtimes.status_for_format("onnx")
check("应用环境 onnxruntime 可用（S1 主路径）", ort_status.available, str(ort_status))
check("已装运行时能报出版本", bool(ort_status.version), str(ort_status.version))

ov_status = runtimes.status_for_format("openvino_ir")
check("openvino_ir 的运行时状态是「可查询」而非异常", ov_status.available is False)
check("缺 openvino 时给的是可照做的安装建议",
      bool(ov_status.reason) and "openvino" in ov_status.reason, str(ov_status.reason))
check("openvino 被标为可选（缺失不算故障）", ov_status.optional is True)

ncnn_status = runtimes.status_for_format("ncnn")
check("缺 ncnn 时状态同样可查询且有建议",
      ncnn_status.available is False and bool(ncnn_status.reason), str(ncnn_status.reason))
check("ncnn 被标为可选", ncnn_status.optional is True)

pth_status = runtimes.status_for_format("pth")
check("pth 归因为「需离线转换」而非「运行时缺失」",
      pth_status.available is False and "离线转换" in (pth_status.reason or ""), str(pth_status.reason))

check("未知格式不抛异常，返回不可用", runtimes.status_for_format("nope").available is False)
check("探测不存在的模块返回 False（不抛）", runtimes.module_available("websr_no_such_module_xyz") is False)
_rt_src = (ROOT / "server" / "app" / "engine" / "runtimes.py").read_text(encoding="utf-8")
# 必须用**行首锚定**的正则：文档里会写到 `import openvino` 这种说明文字，
# 朴素子串（`"import openvino" in src`）会把注释当代码，误报纯度违规。
_NO_HEAVY_IMPORT = re.compile(r"^\s*(?:import|from)\s+(?:onnxruntime|openvino|ncnn)\b", re.M)
check("runtimes 只查 spec、不 import 运行时本尊（探测不能变瓶颈）",
      _NO_HEAVY_IMPORT.search(_rt_src) is None, str(_NO_HEAVY_IMPORT.findall(_rt_src)))

# ---------------------------------------------------------------------------
section("2. 格式路由（ADR-003 确定性判定）")
# ---------------------------------------------------------------------------
r_onnx = model_loader.route_for("onnx")
check("onnx：无配套、走 ORT",
      r_onnx.primary_ext == ".onnx" and r_onnx.companion_ext is None and r_onnx.loader == "ort")
r_ir = model_loader.route_for("openvino_ir")
check("IR：.xml + .bin、走 OpenVINO 原生",
      (r_ir.primary_ext, r_ir.companion_ext, r_ir.loader) == (".xml", ".bin", "openvino"))
check("IR 路由说明写明「不用 ORT + OV EP」", "ORT" in r_ir.note and "慢" in r_ir.note)
r_ncnn = model_loader.route_for("ncnn")
check("ncnn：.param + .bin、走 ncnn 运行时",
      (r_ncnn.primary_ext, r_ncnn.companion_ext, r_ncnn.loader) == (".param", ".bin", "ncnn"))
check("ncnn 路由说明写明「层覆盖失败即剔除」", "剔除" in r_ncnn.note)
check("pth / safetensors 的 loader 为 none（不加载）",
      model_loader.route_for("pth").loader == "none"
      and model_loader.route_for("safetensors").loader == "none")

try:
    model_loader.route_for("weird")
    check("未知格式抛 ModelLoadError", False, "未抛异常")
except model_loader.ModelLoadError as exc:
    check("未知格式抛 ModelLoadError(unsupported_format)",
          exc.code == model_loader.UNSUPPORTED_FORMAT and bool(exc.to_dict()["reason"]))

# ---------------------------------------------------------------------------
section("3. 加载失败分支（每条都要有可判别的码）")
# ---------------------------------------------------------------------------

def _load_err(spec: model_loader.LoadSpec) -> model_loader.ModelLoadError | None:
    try:
        model_loader.load_backend(spec)
    except model_loader.ModelLoadError as exc:
        return exc
    return None


err = _load_err(model_loader.LoadSpec(path=ROOT / "x.pth", fmt="pth"))
check(".pth → needs_convert 且指向转换",
      err is not None and err.code == model_loader.NEEDS_CONVERT
      and err.detail.get("action") == "convert_to_onnx", str(err and err.to_dict()))

err = _load_err(model_loader.LoadSpec(path=ROOT / "x.safetensors", fmt="safetensors"))
check(".safetensors → needs_convert",
      err is not None and err.code == model_loader.NEEDS_CONVERT, str(err and err.code))

err = _load_err(model_loader.LoadSpec(path=ROOT / "nope.onnx", fmt="onnx"))
check("文件不存在 → file_missing",
      err is not None and err.code == model_loader.FILE_MISSING, str(err and err.code))

err = _load_err(model_loader.LoadSpec(path=IR_XML, fmt="openvino_ir", companion=None))
check("IR 缺 .bin 配套 → companion_missing",
      err is not None and err.code == model_loader.COMPANION_MISSING, str(err and err.code))

err = _load_err(model_loader.LoadSpec(path=IR_BIN, fmt="onnx"))
check(".bin 作主文件 → companion_missing（不能单文件加载）",
      err is not None and err.code == model_loader.COMPANION_MISSING, str(err and err.code))

# 主文件必须真实存在，否则会先撞上 file_missing（结构性校验里文件存在先于配套齐全）。
_NCNN_PARAM = NCNN_DIR / "realesrgan-x4plus.param"
err = _load_err(model_loader.LoadSpec(path=_NCNN_PARAM, fmt="ncnn", companion=None))
check("ncnn 缺 .bin 配套 → companion_missing",
      err is not None and err.code == model_loader.COMPANION_MISSING, str(err and err.code))

# 校验顺序回归：本机没有 openvino、也没有 ncnn，但**制品残缺**必须优先报出来。
# 否则同一份残缺模型在不同机器上会得到不同错误码，companion 分支在应用环境也不可观测。
_no_rt = runtimes.module_available("openvino") is False and runtimes.module_available("ncnn") is False
_broken = _load_err(model_loader.LoadSpec(path=IR_XML, fmt="openvino_ir", companion=None))
check("残缺制品在任何机器上同码（结构性校验先于环境性校验）",
      _no_rt and _broken is not None and _broken.code == model_loader.COMPANION_MISSING,
      f"本机 openvino/ncnn 均未装={_no_rt}，残缺 IR 却报 {_broken and _broken.code}")

err = _load_err(model_loader.LoadSpec(path=IR_XML, fmt="openvino_ir", companion=IR_BIN))
check("IR 配套齐全但应用环境无 openvino → runtime_missing（不是 load_failed）",
      err is not None and err.code == model_loader.RUNTIME_MISSING
      and err.detail.get("optional") is True, str(err and err.to_dict()))
check("runtime_missing 的原因里带可照做的建议",
      err is not None and "openvino" in (err.reason or ""), str(err and err.reason))

err = _load_err(model_loader.LoadSpec(
    path=NCNN_DIR / "realesrgan-x4plus.param", fmt="ncnn",
    companion=NCNN_DIR / "realesrgan-x4plus.bin"))
check("ncnn 配套齐全但应用环境无 ncnn → runtime_missing",
      err is not None and err.code == model_loader.RUNTIME_MISSING, str(err and err.to_dict()))

# ---------------------------------------------------------------------------
section("4. ONNX 输入约束读取（兑现 T-804 的 align 挂账）")
# ---------------------------------------------------------------------------
spec_main = model_introspect.spec_for_path(MAIN_ONNX)
check("主模型：动态 H/W", spec_main.dynamic_hw is True and spec_main.source == "dynamic")
check("主模型：未检测到对齐约束 → align=1", spec_main.align == 1)
check("主模型：fixed_tile 为空", spec_main.fixed_tile is None)
check("主模型：输入 dtype = float32", spec_main.dtype == "float32")
check("动态输入的原因里说明「读不出窗口约束，不猜」",
      "读不出" in spec_main.reason and "元信息" in spec_main.reason, spec_main.reason)

spec_fp16 = model_introspect.spec_for_path(FP16_ONNX)
check("fp16 变体的输入 dtype 被如实读出 = float16", spec_fp16.dtype == "float16", spec_fp16.dtype)

spec_s512 = model_introspect.spec_for_path(S512_ONNX)
check("静态变体：source=static", spec_s512.source == "static")
check("静态变体：fixed_tile=512（硬约束）", spec_s512.fixed_tile == 512, str(spec_s512.fixed_tile))
check("静态变体的原因写明「必须正好等于 512」", "512" in spec_s512.reason, spec_s512.reason)

again = model_introspect.spec_for_path(MAIN_ONNX)
check("约束结果进程内缓存（同对象）", again is spec_main)
check("读不存在的文件不抛异常，返回 unknown",
      model_introspect.spec_for_path(ROOT / "nope.onnx").source == "unknown")

_bad = OUT / "not_a_model.onnx"
_bad.write_bytes(b"this is not an onnx file")
check("非法 onnx 内容不抛异常，返回 unknown",
      model_introspect.spec_for_path(_bad).source == "unknown")

# ---------------------------------------------------------------------------
section("5. ORT 后端：加载与推理")
# ---------------------------------------------------------------------------
import numpy as np  # noqa: E402

be = model_loader.load_backend(model_loader.LoadSpec(
    path=MAIN_ONNX, fmt="onnx", scale=4, backend="CPUExecutionProvider"))
d = be.describe()
check("describe 报出真实 provider", d["providers"] == ["CPUExecutionProvider"], str(d["providers"]))
check("describe 报出输入 dtype 与精度", d["input_dtype"] == "float32" and d["precision_effective"] == "fp32")
check("describe 带输入契约（供几何校正用）", isinstance(d.get("input_spec"), dict))
check("后端 kind 正确", be.kind == "onnxruntime")

x = np.zeros((1, 3, 64, 64), dtype=np.float32)
x[0, 0] = np.linspace(0, 1, 64)[None, :]
y = be.infer(x)
check("ORT 推理输出形状 = 输入 × scale", np.asarray(y).shape == (1, 3, 256, 256), str(np.asarray(y).shape))
check("输出无 NaN", int(np.isnan(np.asarray(y)).sum()) == 0)
check("输出不是常量（真的算了）", float(np.asarray(y).std()) > 1e-4)
be.close()
try:
    be.infer(x)
    check("close 后再 infer 抛 ModelLoadError", False, "未抛")
except model_loader.ModelLoadError as exc:
    check("close 后再 infer 抛 ModelLoadError(INFERENCE_FAILED)",
          exc.code == model_loader.INFERENCE_FAILED)

be16 = model_loader.load_backend(model_loader.LoadSpec(
    path=FP16_ONNX, fmt="onnx", scale=4, backend="CPUExecutionProvider"))
check("fp16 模型 describe 报 float16", be16.describe()["input_dtype"] == "float16")
y16 = be16.infer(x)  # 送 float32 进去：加载器按模型 dtype 自动转换
check("fp16 模型接受 float32 输入（按模型 dtype 自动转换）",
      np.asarray(y16).shape == (1, 3, 256, 256), str(np.asarray(y16).shape))
be16.close()

# ---------------------------------------------------------------------------
section("6. pipeline 全链路：真实产物")
# ---------------------------------------------------------------------------

def make_image(w: int, h: int, path: Path) -> None:
    from PIL import Image

    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[..., 0] = np.linspace(0, 255, w)[None, :].astype(np.uint8)
    arr[..., 1] = np.linspace(0, 255, h)[:, None].astype(np.uint8)
    yy, xx = np.mgrid[0:h, 0:w]
    arr[..., 2] = ((xx * 7 + yy * 5) % 256).astype(np.uint8)  # 高频纹理
    px = path
    px.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr).save(px)


src_big = OUT / "src_96x72.png"
make_image(96, 72, src_big)
out_big = OUT / "out_96x72_x4.png"

be = model_loader.load_backend(model_loader.LoadSpec(
    path=MAIN_ONNX, fmt="onnx", scale=4, backend="CPUExecutionProvider"))
events: list[tuple[int, int, str, str]] = []
res = pipeline.run_upscale(be, pipeline.UpscaleRequest(
    source_path=src_big, output_path=out_big,
    tile=64, overlap=16, feather_px=16, scale=4, spec=PreprocessSpec(),
    report=lambda a, b, c, e: events.append((a, b, c, e)),
))
be.close()

check("产物尺寸 = 原图 × scale", (res.width, res.height) == (384, 288), f"{res.width}x{res.height}")
check("产物记录了源图尺寸", (res.source_width, res.source_height) == (96, 72))
check("多块路径：块数与计划一致（96/72 vs tile64 → 2×2）", res.tiles == 4, str(res.tiles))
check("产物文件真实存在", out_big.is_file())

from PIL import Image  # noqa: E402

with Image.open(out_big) as im:
    im.load()
    arr_out = np.asarray(im.convert("RGB"), dtype=np.float32)
check("产物可读回且尺寸一致", arr_out.shape[:2] == (288, 384), str(arr_out.shape))
check("产物不是全黑/全白（std > 1）", float(arr_out.std()) > 1.0, f"std={arr_out.std():.2f}")
check("产物 sha256 与文件实际一致",
      res.sha256 == __import__("hashlib").sha256(out_big.read_bytes()).hexdigest())
check("产物 size_bytes 与文件一致", res.size_bytes == out_big.stat().st_size)

stages = [e[2] for e in events]
check("进度覆盖四个阶段",
      {"preprocessing", "inferencing", "stitching", "saving"} <= set(stages), str(sorted(set(stages))))
chunks = [e[0] for e in events if e[2] == "inferencing"]
check("chunk 级进度单调不减", chunks == sorted(chunks), str(chunks))
check("最后一块 = 总块数", bool(chunks) and chunks[-1] == res.tiles, str(chunks))

# ---- T-702 回归：非分块阶段不得上报块计数 -------------------------------------
# 缺陷（T-702 联调实测）：预处理曾上报 (1, 1)，服务端 percent 公式
#   percent = (progress_done + current_chunk/total_chunks) / total_items
# 在 total_items=1 时算出 1.0 → 进度条在预处理阶段即冲到 100%，随后回落到 1/35，
# **违反契约 §5「percent 单调不减」**。控制面测试注入 StubExecutor，覆盖不到真实路径。
pre_chunk = [(a, b) for a, b, c, _e in events if c == "preprocessing"]
check("预处理阶段上报块计数 total=0（无块语义）【T-702 回归】",
      bool(pre_chunk) and all(b == 0 for _a, b in pre_chunk), str(pre_chunk))
check("预处理阶段不出现「done>=total>0」的假 100%【T-702 回归】",
      all(not (b > 0 and a >= b) for a, b in pre_chunk), str(pre_chunk))
chunk_events = [(a, b) for a, b, c, _e in events if c in ("inferencing", "stitching", "saving")]
check("分块阶段才有块计数（total>0）", all(b > 0 for _a, b in chunk_events[:1]), str(chunk_events[:1]))
check("执行事实里带后端描述", res.backend.get("kind") == "onnxruntime", str(res.backend)[:120])
check("执行事实里带耗时", res.elapsed_ms > 0, str(res.elapsed_ms))

# 小图（短边 < tile）：单块 + 居中补齐，尺寸仍为 原图 × scale
src_small = OUT / "src_40x30.png"
make_image(40, 30, src_small)
out_small = OUT / "out_40x30_x4.png"
be = model_loader.load_backend(model_loader.LoadSpec(
    path=MAIN_ONNX, fmt="onnx", scale=4, backend="CPUExecutionProvider"))
res_small = pipeline.run_upscale(be, pipeline.UpscaleRequest(
    source_path=src_small, output_path=out_small,
    tile=64, overlap=16, feather_px=16, scale=4, spec=PreprocessSpec(),
))
be.close()
check("小图（短边 < tile）走单块路径", res_small.tiles == 1, str(res_small.tiles))
check("小图产物尺寸仍为 原图 × scale", (res_small.width, res_small.height) == (160, 120),
      f"{res_small.width}x{res_small.height}")

# 协作式取消：第 2 块之后置位 → 抛 InferCancelled，且**不产出文件**
out_cancel = OUT / "out_cancel.png"
if out_cancel.exists():
    out_cancel.unlink()
state = {"n": 0}


def _cancel_after_two(done: int, total: int, stage: str, msg: str) -> None:
    if stage == "inferencing":
        state["n"] += 1


be = model_loader.load_backend(model_loader.LoadSpec(
    path=MAIN_ONNX, fmt="onnx", scale=4, backend="CPUExecutionProvider"))
cancelled = False
try:
    pipeline.run_upscale(be, pipeline.UpscaleRequest(
        source_path=src_big, output_path=out_cancel,
        tile=64, overlap=16, feather_px=16, scale=4, spec=PreprocessSpec(),
        report=_cancel_after_two, should_cancel=lambda: state["n"] >= 2,
    ))
except pipeline.InferCancelled:
    cancelled = True
be.close()
check("取消抛 InferCancelled（协作式，不强杀）", cancelled)
check("取消后不产出产物文件（不是半个文件）", not out_cancel.exists())

# ---------------------------------------------------------------------------
section("7. EngineExecutor：HTTP 端到端产出真实 ARTIFACT")
# ---------------------------------------------------------------------------
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.tasks import executor as task_executor  # noqa: E402


def wait_status(client, task_id: str, targets=("completed", "failed", "canceled"), timeout=180.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = client.get(f"/api/tasks/{task_id}").json()
        if t["status"] in targets:
            return t
        time.sleep(0.3)
    raise TimeoutError(f"{task_id} 未在 {timeout}s 内进入 {targets}")


check("默认执行器是 EngineExecutor（真实推理）",
      isinstance(task_executor.get_executor(), task_executor.EngineExecutor))
task_executor.set_executor_factory(task_executor.StubExecutor)
check("显式注入点可把执行器换成 Stub（控制面验证用）",
      isinstance(task_executor.get_executor(), task_executor.StubExecutor))
task_executor.reset_executor_factory()
check("reset 后回到 EngineExecutor",
      isinstance(task_executor.get_executor(), task_executor.EngineExecutor))

with TestClient(app) as client:
    buf = io.BytesIO()
    make_image(100, 80, OUT / "src_100x80.png")
    Image.open(OUT / "src_100x80.png").save(buf, "PNG")
    up = client.post("/api/files/upload", files={"file": ("sample.png", buf.getvalue(), "image/png")})
    check("上传成功", up.status_code == 201, str(up.status_code))
    file_id = up.json()["file_id"]

    models = client.get("/api/models").json()
    target = next((m for m in models if m["format"] == "onnx"), None)
    check("内置 onnx 模型已登记（真实文件）", target is not None, str([m["id"] for m in models]))

    created = client.post("/api/tasks", json={
        "type": "upscale", "file_id": file_id,
        "params": {"scale": 4, "model_id": target["id"], "auto": True,
                   "tile": None, "precision": None, "backend": None},
    })
    check("提交任务返回 201", created.status_code == 201, str(created.status_code))
    tid = created.json()["id"]
    t = wait_status(client, tid)
    check("真实推理任务完成", t["status"] == "completed", f"{t['status']} {t['error']}")

    ex = (t["resolved"] or {}).get("execution")
    check("resolved.execution 记录了真实执行事实", isinstance(ex, dict) and ex.get("tiles", 0) >= 1, str(ex)[:200])
    check("execution 记录了真实块数与耗时",
          ex.get("tiles") == 4 and ex.get("elapsed_ms", 0) > 0, str(ex)[:200])
    check("execution 记录了后端真实描述",
          (ex.get("backend") or {}).get("kind") == "onnxruntime", str((ex or {}).get("backend"))[:160])

    check("TaskOut.output_width/height 已回填", t["output_width"] == 400 and t["output_height"] == 320,
          f"{t['output_width']}x{t['output_height']}")
    check("源图尺寸来自上传元信息", t["source_width"] == 100 and t["source_height"] == 80)

    arts = client.get(f"/api/tasks/{tid}/artifacts").json()
    check("产物列表有 1 条 output", len(arts) == 1 and arts[0]["kind"] == "output", str(arts))
    check("产物 path 带 data/ 前缀且指向 outputs/tsk_（与前端约定一致）",
          arts[0]["path"].startswith("data/outputs/tsk_"), arts[0]["path"])
    check("产物记录了 sha256 与大小",
          len(arts[0]["sha256"] or "") == 64 and arts[0]["size_bytes"] > 0)
    check("产物宽高 = 输出尺寸", arts[0]["width"] == 400 and arts[0]["height"] == 320)

    disk = DATA / arts[0]["path"][len("data/"):]
    check("产物文件在磁盘上真实存在", disk.is_file(), str(disk))

    listed = next(x for x in client.get("/api/tasks").json() if x["id"] == tid)
    check("列表里的 output_width 同样回填", listed["output_width"] == 400)

    # 再跑一次：确认重复执行不冲突（并发槽已释放）
    created2 = client.post("/api/tasks", json={
        "type": "upscale", "file_id": file_id,
        "params": {"scale": 4, "model_id": target["id"], "auto": True,
                   "tile": None, "precision": None, "backend": None},
    })
    check("真实推理完成后可立即提交下一个任务（并发槽已释放）",
          created2.status_code == 201, str(created2.status_code))
    t2 = wait_status(client, created2.json()["id"])
    check("第二个任务同样完成", t2["status"] == "completed", f"{t2['status']} {t2['error']}")

# ---------------------------------------------------------------------------
section("8. 执行时才发现的偏差必须显式登记")
# ---------------------------------------------------------------------------
EE = task_executor.EngineExecutor

# 8.1 精度：决策 fp16 但模型是 fp32 → 记一条降档（不做运行时精度转换）
class _P:
    precision = "fp16"

_reasons: list[str] = []
_dg: list[dict] = []
EE._reconcile_precision({"precision_effective": "fp32"}, profile=_P(),
                        reasons=_reasons, downgrades=_dg)
check("决策 fp16 + 模型 fp32 → 记一条 precision 降档",
      len(_dg) == 1 and _dg[0]["field"] == "precision" and _dg[0]["to"] == "fp32", str(_dg))
check("该降档写明「不做运行时精度转换」", "不做精度转换" in _dg[0]["reason"], _dg[0]["reason"])

# 8.2 精度：模型是 fp16 而决策 fp32 → 只记说明，不算降级
# 注意 `_P` 的 precision 是 fp16（上一条用它验"决策 fp16 撞上 fp32 模型"），
# 本条要验的是相反方向，必须另换一个 fp32 的决策档，否则 desc 与 profile 相等会走提前返回。
class _P_fp32:
    precision = "fp32"

_r2: list[str] = []
_d2: list[dict] = []
EE._reconcile_precision({"precision_effective": "fp16"}, profile=_P_fp32(),
                        reasons=_r2, downgrades=_d2)
check("模型 fp16 + 决策 fp32 → 不记降档（不是保守动作）", _d2 == [], str(_d2))
check("但记了「以模型声明为准」的说明", any("以模型声明为准" in x for x in _r2), str(_r2))

# 8.2b 决策与模型精度一致 → 既不降档也不加说明（无事发生，不刷日志）
_r2b: list[str] = []
_d2b: list[dict] = []
EE._reconcile_precision({"precision_effective": "fp16"}, profile=_P(),
                        reasons=_r2b, downgrades=_d2b)
check("决策与模型精度一致 → 不降档也不写说明",
      _d2b == [] and _r2b == [], f"{_d2b} {_r2b}")

# 8.3 几何：静态 512 模型被 64 的决策撞上 → tile 校正 + 降档
_r3: list[str] = []
_d3: list[dict] = []
tile3, ov3, fe3 = EE._reconcile_geometry(
    {"input_spec": {"fixed_tile": 512}}, tile=64, overlap=16, reasons=_r3, downgrades=_d3)
check("静态 512 模型：tile 被校正为 512", tile3 == 512, str(tile3))
check("校正后 overlap 被重算且小于 tile（不退化硬切）",
      0 < ov3 < tile3 and ov3 == overlap_for(512, DEFAULT_ALIGN), f"{ov3}")
check("几何校正记了一条降档", len(_d3) == 1 and _d3[0]["field"] == "tile", str(_d3))

# 8.4 后端：请求的 GPU EP 未进入 session → 记降档（配置漂移不静默）
class _P2:
    backend = "CUDAExecutionProvider"

_r4: list[str] = []
_d4: list[dict] = []
EE._reconcile_backend({"providers": ["CPUExecutionProvider"]}, profile=_P2(),
                      reasons=_r4, downgrades=_d4)
check("请求 CUDA 但只有 CPU → 记一条 backend 降档",
      len(_d4) == 1 and _d4[0]["field"] == "backend", str(_d4))

_r5: list[str] = []
_d5: list[dict] = []
EE._reconcile_backend({"providers": ["CPUExecutionProvider"]},
                      profile=type("_P3", (), {"backend": "CPUExecutionProvider"})(),
                      reasons=_r5, downgrades=_d5)
check("请求 CPU 且 CPU 在位 → 不误报降级", _d5 == [], str(_d5))

# 8.5 real pipeline：s512 静态模型端到端（tile 由约束决定）
if S512_ONNX.is_file():
    src64 = OUT / "src_64x64.png"
    make_image(64, 64, src64)
    out512 = OUT / "out_64x64_x4_fixed.png"
    be512 = model_loader.load_backend(model_loader.LoadSpec(
        path=S512_ONNX, fmt="onnx", scale=4, backend="CPUExecutionProvider"))
    ctx_profile = None
    res512 = pipeline.run_upscale(be512, pipeline.UpscaleRequest(
        source_path=src64, output_path=out512,
        tile=512, overlap=128, feather_px=128, scale=4, spec=PreprocessSpec(),
    ))
    be512.close()
    check("静态 512 模型端到端出图", out512.is_file() and res512.width == 256,
          f"{res512.width}x{res512.height}")
else:
    skip("静态 512 模型端到端", "基准变体文件不在（不登记入产品模型库）")

# ---------------------------------------------------------------------------
section("9. 引擎纯度与源码级检查")
# ---------------------------------------------------------------------------
_SRC = ROOT / "server" / "app" / "engine"
# 行首锚定的正则：docstring 里出现 "engine/ 不 import fastapi / sqlalchemy / pydantic"
# 这类**说明文字**时，朴素子串检查会把它当 import 语句误报（T-804 踩过同一个坑）。
_APP_IMPORT = re.compile(r"^\s*(?:import|from)\s+(fastapi|sqlalchemy|pydantic)\b", re.M)
for name in ("runtimes.py", "model_introspect.py", "model_loader.py", "pipeline.py"):
    src = (_SRC / name).read_text(encoding="utf-8")
    bad = _APP_IMPORT.findall(src)
    check(f"{name} 不 import 应用层（引擎纯度）", not bad, str(bad))
    models = [w for w in ("3050", "4090", "RTX ", "GTX ", "GeForce") if w in src]
    check(f"{name} 不含设备型号字面量（ADR-004 原则 2）", not models, str(models))

_mgr_src = (ROOT / "server" / "app" / "tasks" / "manager.py").read_text(encoding="utf-8")
check("管理器不自己决定参数（决策来自 engine_decision）",
      "engine_decision.decide_for_task" in _mgr_src)
check("管理器把产物落库（T-806 起有真实 ARTIFACT）", "Artifact(" in _mgr_src)

_exe_src = (ROOT / "server" / "app" / "tasks" / "executor.py").read_text(encoding="utf-8")
check("执行器默认返回真实推理实现", "return EngineExecutor()" in _exe_src)
check("执行器不按设备型号分支", not any(w in _exe_src for w in ("3050", "4090", "RTX ", "GeForce")))
check("测试注入点不依赖环境变量（避免线上行为被环境改变）",
      "os.environ" not in _exe_src and "getenv" not in _exe_src)

# ---------------------------------------------------------------------------
section("10. 跨环境复核：IR 与 ncnn 的真实推理（子进程）")
# ---------------------------------------------------------------------------
PROBE = ROOT / "scripts" / "test-script" / "_t806_backend_probe.py"


def run_probe(venv: str, fmt: str, path: Path, companion: Path | None,
              *, size: int, tile: int = 0, overlap: int = 0, timeout: int = 600) -> dict | None:
    py = ROOT / ".venvs" / venv / "Scripts" / "python.exe"
    if not py.is_file() or not path.is_file() or (companion and not companion.is_file()):
        return None
    cmd = [str(py), str(PROBE), "--fmt", fmt, "--path", str(path), "--size", str(size)]
    if companion:
        cmd += ["--companion", str(companion)]
    if tile:
        cmd += ["--tile", str(tile), "--overlap", str(overlap)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              cwd=str(ROOT), encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": {"code": "timeout"}}
    line = next((ln for ln in reversed(proc.stdout.splitlines()) if ln.strip().startswith("{")), "")
    try:
        return json.loads(line)
    except Exception:
        return {"ok": False, "error": {"code": "unparsable", "message": proc.stdout[-600:],
                                       "stderr": proc.stderr[-600:]}}


ir_report = run_probe("sr-ov", "openvino_ir", IR_XML, IR_BIN, size=64, tile=64, overlap=16)
if ir_report is None:
    skip("sr-ov 里跑 IR 全链路", "venv 或 IR 模型文件缺失")
else:
    check("【sr-ov】OpenVINO IR 加载成功并跑通全链路",
          ir_report.get("ok") is True, json.dumps(ir_report.get("error", {}), ensure_ascii=False)[:300])
    if ir_report.get("ok"):
        oc = ir_report["outcome"]
        check("【sr-ov】IR 产物尺寸 = 原图 × 4", (oc["width"], oc["height"]) == (256, 256),
              f"{oc['width']}x{oc['height']}")
        check("【sr-ov】IR 走的是 OpenVINO 原生（kind=openvino）",
              oc["backend"].get("kind") == "openvino", str(oc["backend"])[:160])
        _ir_out = Path(oc["output_path"])
        if _ir_out.is_file():
            with Image.open(_ir_out) as im:
                im.load()
                _ir_arr = np.asarray(im.convert("RGB"), dtype=np.float32)
            check("【sr-ov】IR 产物非全黑（ncnn 类黑图 bug 未出现）",
                  float(_ir_arr.std()) > 1.0, f"std={_ir_arr.std():.2f}")
            check("【sr-ov】IR 产物尺寸与读回一致", _ir_arr.shape[:2] == (256, 256), str(_ir_arr.shape))
        else:
            check("【sr-ov】IR 产物文件存在", False, str(_ir_out))

ncnn_report = run_probe("sr-ncnn", "ncnn", NCNN_DIR / "realesrgan-x4plus.param",
                        NCNN_DIR / "realesrgan-x4plus.bin", size=64)
if ncnn_report is None:
    skip("sr-ncnn 里跑 ncnn 单块推理", "venv 或 ncnn 模型文件缺失")
else:
    check("【sr-ncnn】ncnn 加载成功并真实推理",
          ncnn_report.get("ok") is True, json.dumps(ncnn_report.get("error", {}), ensure_ascii=False)[:300])
    if ncnn_report.get("ok"):
        check("【sr-ncnn】ncnn 输出形状 = 输入 × 4", ncnn_report.get("shape") == [1, 3, 256, 256],
              str(ncnn_report.get("shape")))
        st = ncnn_report.get("stats") or {}
        check("【sr-ncnn】ncnn 输出非全黑（T-210 结论复现）",
              st.get("std", 0) > 0.01 and st.get("nan", 1) == 0, str(st))

# ---------------------------------------------------------------------------
print(f"\n===== 结果：{PASS} 通过 / {FAIL} 失败 / {SKIP} 跳过 =====")
shutil.rmtree(DATA, ignore_errors=True)
raise SystemExit(1 if FAIL else 0)
