"""T-807 `.pth` / `.safetensors` → `.onnx` 离线转换（ADR-003 方案 A）验证。

运行：./.venvs/sr-app/Scripts/python.exe scripts/test-script/verify_t807_convert.py

覆盖：
  1. **依赖分离**（本任务的立身之本）：产品环境不含 torch；产品源码不 import torch/spandrel/tools
  2. 转换工具 CLI 契约（`--list-arch` / 缺文件 / `--json` 真实转换）
  3. 工具失败分支（跨环境子进程跑 `_t807_tool_probe.py`：架构认不出 / 自检不通过 / 导出失败）
  4. 服务层失败码（环境缺失 / 非可转换格式 / 源模型不存在）
  5. **HTTP 端到端**：导入真实 `.pth` → 触发转换 → 产物登记 → **产品加载器真的能加载并推理**
  6. 幂等与输出形状（重复触发复用 / `ModelOut.conversion` 仅对两种格式出现）
  7. **动态 H/W**（产品侧能自由分块的前提）
  8. 源码级检查

前置资产：`.workbuddy/verify/t807/realesr-general-x4v3.pth`（4.7 MB，Real-ESRGAN v0.2.5.0 官方发布，
SRVGGNetCompact，BSD-3-Clause，可随仓库分发）。缺失则**跳过**端到端并给出获取方式
——不联网、不假装通过。

隔离：数据根建在**系统临时目录**（不在仓库内）。
"""
import importlib.util
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
TMP = Path(tempfile.mkdtemp(prefix="websr_t807_"))
DATA = TMP / "data"
DATA.mkdir(parents=True, exist_ok=True)

os.environ["APP_DATA_DIR"] = str(DATA)
os.environ["APP_DB_URL"] = f"sqlite:///{(DATA / 'app.db').as_posix()}"

sys.path.insert(0, str(ROOT / "server"))

from app.engine.runtime_env import prepare_dll_paths  # noqa: E402

prepare_dll_paths()

import numpy as np  # noqa: E402

PASS = FAIL = SKIP = 0
CONVERT_PY = ROOT / ".venvs" / "sr-convert" / "Scripts" / "python.exe"
TOOL = ROOT / "tools" / "convert_to_onnx.py"
PROBE = ROOT / "scripts" / "test-script" / "_t807_tool_probe.py"
FIXTURE = ROOT / ".workbuddy" / "verify" / "t807" / "realesr-general-x4v3.pth"


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


def skip(name: str, why: str) -> None:
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {name} —— {why}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def run_tool(*args: str) -> tuple[subprocess.CompletedProcess, dict | None]:
    """以子进程调用转换工具（与产品侧 conversion_service 同一路径）。"""
    proc = subprocess.run(
        [str(CONVERT_PY), str(TOOL), *args],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    payload = None
    try:
        payload = json.loads(proc.stdout)
    except Exception:
        pass
    return proc, payload


# ---------------------------------------------------------------------------
section("1. 依赖分离 —— ADR-003：应用内不捆绑 PyTorch")

check("转换环境解释器存在", CONVERT_PY.is_file(), str(CONVERT_PY))
check("转换工具存在", TOOL.is_file(), str(TOOL))
check("当前（产品）环境**不含 torch** —— 依赖分离的核心不变量",
      importlib.util.find_spec("torch") is None,
      f"sr-app 里竟然找到了 torch：{getattr(importlib.util.find_spec('torch'), 'origin', '?')}")
check("产品环境不含 spandrel", importlib.util.find_spec("spandrel") is None)

_IMPORT_HEAVY = re.compile(r"^\s*(?:import|from)\s+(?:torch|torchvision|spandrel|safetensors)\b", re.M)
_IMPORT_TOOLS = re.compile(r"^\s*(?:import|from)\s+tools\b", re.M)

_offenders = []
for p in (ROOT / "server" / "app").rglob("*.py"):
    for m in _IMPORT_HEAVY.finditer(p.read_text(encoding="utf-8")):
        _offenders.append(f"{p.relative_to(ROOT)}: {m.group(0).strip()}")
check("产品代码不 import torch / spandrel / safetensors（行首锚定正则）",
      not _offenders, str(_offenders[:4]))

_tools_offenders = []
for p in (ROOT / "server" / "app").rglob("*.py"):
    for m in _IMPORT_TOOLS.finditer(p.read_text(encoding="utf-8")):
        _tools_offenders.append(f"{p.relative_to(ROOT)}: {m.group(0).strip()}")
check("产品代码不 import tools/（开发期资产，产品不得依赖）",
      not _tools_offenders, str(_tools_offenders[:4]))

# 工具模块本身可在无 torch 的环境 import（否则产品侧连"探测可用性"都做不到）
_boot = subprocess.run(
    [sys.executable, "-c",
     f"import sys; sys.path.insert(0, {str(ROOT / 'tools')!r}); "
     "import convert_to_onnx as t; "
     "print('torch' in sys.modules, hasattr(t, 'convert'), hasattr(t, 'main'))"],
    capture_output=True, text=True, cwd=str(ROOT),
)
check("工具模块在无 torch 环境可 import，且未连带加载 torch",
      _boot.returncode == 0 and _boot.stdout.strip().startswith("False True True"),
      f"rc={_boot.returncode} out={_boot.stdout.strip()!r} err={_boot.stderr[-200:]!r}")

conv_svc_src = (ROOT / "server" / "app" / "services" / "conversion_service.py").read_text(encoding="utf-8")
check("服务层不 import torch / spandrel", not _IMPORT_HEAVY.search(conv_svc_src))
check("服务层以**子进程**调用工具（不 import 转换工具）",
      "subprocess.run(" in conv_svc_src and "import convert_to_onnx" not in conv_svc_src)


# ---------------------------------------------------------------------------
section("2. 转换工具 CLI 契约")

proc, _ = run_tool("--list-arch")
check("--list-arch 退出码 0", proc.returncode == 0, f"rc={proc.returncode}")
arches = [ln for ln in proc.stdout.splitlines() if ln.strip()]
check("列出 spandrel 支持的架构（非空且规模合理）", len(arches) >= 20, f"共 {len(arches)} 条")
check("架构表含 Real-ESRGAN 系（本次验证用到的）",
      any("ESRGAN" in a for a in arches), str(arches[:5]))

proc, payload = run_tool(str(TMP / "nope.pth"), "--json")
check("缺文件 → 退出码 2 且 code=file_missing",
      proc.returncode == 2 and bool(payload) and payload.get("code") == "file_missing",
      f"rc={proc.returncode} payload={str(payload)[:120]}")

out_onnx = TMP / "cli_compact.onnx"
_have_fixture = FIXTURE.is_file()
if not _have_fixture:
    skip("真实转换（CLI + HTTP 端到端）",
         f"缺少前置资产 {FIXTURE.relative_to(ROOT)}；可从 Real-ESRGAN v0.2.5.0 发布页取 "
         "realesr-general-x4v3.pth（4.7 MB）放入该路径")
else:
    t0 = time.time()
    proc, payload = run_tool(str(FIXTURE), "-o", str(out_onnx), "--json")
    secs = time.time() - t0
    check("真实转换退出码 0", proc.returncode == 0,
          f"rc={proc.returncode} err={proc.stderr[-300:]}")
    check("--json 结果结构完整（ok/input/output/model/check）",
          bool(payload) and all(k in payload for k in ("ok", "input", "output", "model", "check")),
          str(payload)[:200])
    if payload and payload.get("ok"):
        check("产出 .onnx 且落盘非空",
              out_onnx.is_file() and payload["output"]["size_bytes"] > 0)
        check("识别出的倍数与参数量合理（scale=4，参数量 > 10 万）",
              payload["model"]["scale"] == 4 and payload["model"]["param_count"] > 100_000,
              str(payload["model"])[:200])
        chk = payload.get("check") or {}
        check("**同源自检通过**（max|torch-onnx| < 阈值，且未跳过）",
              chk.get("ok") is True and not chk.get("skipped"), str(chk))
        check("自检给出可复核的量化证据（max_abs_diff / tolerance / 形状）",
              isinstance(chk.get("max_abs_diff"), float)
              and chk["max_abs_diff"] < chk["tolerance"]
              and chk.get("output_shape", [0, 0, 0, 0])[2] == chk["input_size"] * 4,
              str(chk))
        check("导出为**动态 H/W**（产品侧才能自由分块）",
              payload["output"]["dynamic"] is True, str(payload["output"]))
        check("如实报出 spandrel 尺寸约束（不编默认值）",
              "size_requirements" in payload["model"], str(payload["model"].get("size_requirements")))
        check("输出带 sha256 便于登记去重", len(payload["output"]["sha256"]) == 64)
    print(f"  （真实转换耗时 {secs:.1f}s）")


# ---------------------------------------------------------------------------
section("3. 工具失败分支（跨环境子进程：在 sr-convert 里跑工具本尊）")

if not (CONVERT_PY.is_file() and PROBE.is_file() and _have_fixture):
    skip("工具失败分支", "转换环境 / 探针 / 前置权重 三者缺一")
else:
    _p = subprocess.run(
        [str(CONVERT_PY), str(PROBE)], cwd=str(ROOT),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    _tail = (_p.stdout or "").strip().splitlines()
    for ln in _tail:
        if "[PASS]" in ln or "[FAIL]" in ln or "=====" in ln:
            print("  " + ln.strip())
    n_fail = sum(1 for ln in _tail if "[FAIL]" in ln)
    check("【sr-convert】工具级失败分支全部通过", _p.returncode == 0 and n_fail == 0,
          f"rc={_p.returncode} stderr={_p.stderr[-300:]}")


# ---------------------------------------------------------------------------
section("4. 服务层失败码（每条都要能照做）")

from app.db import get_session  # noqa: E402
from app.models.entities import Model  # noqa: E402
from app.services import conversion_service as conv  # noqa: E402

conv.reset_state()
avail = conv.describe_availability()
check("describe_availability() 报可用", avail["available"] is True, str(avail))
check("可用时 reason 为 None（缺什么只在不缺时说）", avail["reason"] is None, str(avail))

_orig_env = conv.env_python
conv.env_python = lambda: None
try:
    r = conv.trigger(1)
    check("转换环境缺失 → started=False 且 code=convert_env_missing",
          r.get("started") is False and r.get("code") == "convert_env_missing", str(r)[:160])
    hint = r.get("reason") or ""
    check("环境缺失时给出**可照做的安装指引**（不是一句 ImportError）",
          "sr-convert" in hint and "uv pip install" in hint)
finally:
    conv.env_python = _orig_env
    conv.reset_state()


# ---------------------------------------------------------------------------
section("5. HTTP 端到端：.pth → 转换 → 登记 → **产品加载器真的能加载并推理**")

from fastapi.testclient import TestClient  # noqa: E402

from app.core import lifecycle  # noqa: E402
from app.main import app  # noqa: E402
from app.services import model_registry as reg  # noqa: E402

# 建表：本脚本**不用** TestClient 的 lifespan——启动序列会投递后台能力探测/标定，
# 与本节要观察的转换作业互相干扰。迁移 + 显式 DLL 注册已足够覆盖被测路径。
lifecycle._run_migrations()
client = TestClient(app)
src_id = None
new_id = None
lst = []

if not _have_fixture:
    skip("HTTP 端到端（导入 → 转换 → 登记 → 加载推理）", "缺少前置权重资产")
else:
    # 5.1 先导入一个可转换格式的模型（导入链是 F-05 既有能力）
    with FIXTURE.open("rb") as fh:
        r = client.post(
            "/api/models/import",
            data={"name": "T807 待转换权重", "format": "pth", "scale": "4",
                  "min_vram_mb": "0", "backends": '["cpu","cuda"]'},
            files={"file": ("upload_src.pth", fh, "application/octet-stream")},
        )
    check("导入 .pth 成功（201）", r.status_code == 201, f"{r.status_code} {r.text[:200]}")
    src = r.json()
    src_id = src.get("id")
    check("导入的 .pth 状态为 needs_convert", src.get("status") == "needs_convert",
          str(src.get("status")))
    check("ModelOut.conversion 对 .pth 非空且可用",
          (src.get("conversion") or {}).get("available") is True, str(src.get("conversion")))
    check("pth 的可加载性如实置为不可用（不假装能直接推理）",
          src.get("available") is False, str(src.get("available")))

    # 5.2 触发 + 轮询
    r2 = client.post(f"/api/models/{src_id}/convert")
    check("POST /convert 返回 202（异步）", r2.status_code == 202,
          f"{r2.status_code} {r2.text[:200]}")
    b2 = r2.json()
    check("触发响应含 started 与 task_id",
          b2.get("started") is True and bool(b2.get("task_id")), str(b2)[:160])

    deadline = time.time() + 300
    state = {}
    while time.time() < deadline:
        state = client.get(f"/api/models/{src_id}/convert").json()
        if state.get("status") in ("completed", "failed"):
            break
        time.sleep(0.5)
    check("转换作业离开 running 态且完成", state.get("status") == "completed",
          f"status={state.get('status')} error={str(state.get('error'))[:400]}")

    if state.get("status") == "completed":
        res = state.get("result") or {}
        new_id = res.get("model_id")
        check("结果给出**产物模型 id**（源模型之外的新模型）",
              bool(new_id) and new_id != src_id, str(res)[:200])
        check("结果带工具侧量化证据（架构 / 自检）",
              bool(res.get("tool")) and (res["tool"].get("check") or {}).get("ok") is True,
              str(res.get("tool"))[:240])

        lst = client.get("/api/models").json()
        rows = {m["id"]: m for m in lst}
        converted = rows.get(new_id)
        check("产物已登记进模型库", converted is not None, f"ids={list(rows)[:6]}")
        if converted:
            check("产物格式为 onnx", converted["format"] == "onnx", converted["format"])
            check("产物状态为 ready（可加载）", converted["status"] == "ready", converted["status"])
            check("产物 available=True（导出侧真实可用）", converted["available"] is True,
                  str(converted.get("unavailable_reason")))
            check("产物路径落在 imported/ 下", "/imported/" in converted["path"], converted["path"])
            check("产物 size_bytes > 0", converted["size_bytes"] > 0)
            check("ModelOut.conversion 对 .onnx 为 null（不需要转换 ≠ 环境没装）",
                  converted.get("conversion") is None, str(converted.get("conversion")))

            # 5.3 闭环：**产品加载器**能否真的加载它并推理
            from app.engine import model_loader  # noqa: E402

            prepare_dll_paths()
            abs_path = reg._abs(converted["path"].split("data/models/", 1)[1])
            check("产物在磁盘上真实存在", abs_path.is_file(), str(abs_path))

            probe_x = np.random.rand(1, 3, 64, 64).astype(np.float32)
            backend = model_loader.load_backend(
                model_loader.LoadSpec(path=abs_path, fmt="onnx", scale=4,
                                      precision="fp32", backend="CPUExecutionProvider")
            )
            y = backend.infer(probe_x)
            backend.close()
            check("**产品加载器能加载转换产物并真实推理**（4× 放大，CPU EP）",
                  getattr(y, "shape", None) == (1, 3, 256, 256), str(getattr(y, "shape", None)))
            check("推理结果非常量且有限（真的算了）",
                  float(np.std(y)) > 1e-6 and bool(np.isfinite(y).all()),
                  f"std={float(np.std(y)):.6f}")

            # 5.4 同一产物在**加速路径**上也能跑（本机已接通 CUDA；不可用则如实标注）
            import onnxruntime as ort  # noqa: E402

            if "CUDAExecutionProvider" in ort.get_available_providers():
                try:
                    gpu = model_loader.load_backend(
                        model_loader.LoadSpec(path=abs_path, fmt="onnx", scale=4,
                                              precision="fp32", backend="CUDAExecutionProvider")
                    )
                    yg = gpu.infer(probe_x)
                    gpu.close()
                    diff = float(np.max(np.abs(y - yg)))
                    check("转换产物在 CUDA EP 上可加载并推理", getattr(yg, "shape", None) == (1, 3, 256, 256),
                          str(getattr(yg, "shape", None)))
                    check("CPU 与 CUDA 结果一致（同图同权重，微小数值差属正常）", diff < 1e-2,
                          f"max|diff|={diff:.3e}")
                except Exception as exc:
                    check("转换产物在 CUDA EP 上可加载并推理", False,
                          f"{type(exc).__name__}: {exc}")
            else:
                skip("CUDA EP 复核", "当前环境无 CUDA EP")
    else:
        check("端到端转换完成", False, str(state.get("error"))[:400])

    # 5.4 幂等：重复触发复用已有产物
    client.post(f"/api/models/{src_id}/convert")
    deadline = time.time() + 180
    st3 = {}
    while time.time() < deadline:
        st3 = client.get(f"/api/models/{src_id}/convert").json()
        if st3.get("status") in ("completed", "failed"):
            break
        time.sleep(0.3)
    check("重复触发复用已有产物（不重复导出）",
          (st3.get("result") or {}).get("reused") is True, str(st3.get("result"))[:200])

    # 5.5 失败码：非可转换格式
    lst = lst or client.get("/api/models").json()
    onnx_ids = [m["id"] for m in lst if m["format"] == "onnx"]
    if onnx_ids:
        r4 = client.post(f"/api/models/{onnx_ids[0]}/convert")
        check("非可转换格式 → 400 且错误码可判别",
              r4.status_code == 400 and "MODEL_NOT_CONVERTIBLE" in r4.text,
              f"{r4.status_code} {r4.text[:200]}")
    r5 = client.post("/api/models/mdl_999999/convert")
    check("源模型不存在 → 404", r5.status_code == 404, str(r5.status_code))


# ---------------------------------------------------------------------------
section("6. 动态 H/W 与尺寸自由度（引擎能自由分块的前提）")

if _have_fixture and out_onnx.is_file():
    from app.engine import model_introspect  # noqa: E402

    spec = model_introspect.spec_for_path(out_onnx)
    check("产物输入被识别为动态 H/W", spec.dynamic_hw is True, str(spec.to_dict())[:240])
    check("动态输入 → fixed_tile 为 None（走引擎自由分块通道）",
          spec.fixed_tile is None, str(spec.fixed_tile))
    check("动态输入 → align=1（未检测到约束；产品侧据此收紧到默认值，不放宽）",
          spec.align == 1, str(spec.align))

    import onnxruntime as ort  # noqa: E402

    sess = ort.InferenceSession(str(out_onnx), providers=["CPUExecutionProvider"])
    sizes = (64, 97, 128, 101)
    ok = []
    for hw in sizes:
        try:
            o = sess.run(None, {"input": np.random.rand(1, 3, hw, hw).astype(np.float32)})[0]
            ok.append(o.shape[2] == hw * 4)
        except Exception as exc:
            print(f"    {hw} 失败：{type(exc).__name__}")
            ok.append(False)
    check("同一产物在多种尺寸可跑（含非 8 倍数 97 / 101）→ 无隐藏步长约束",
          all(ok), f"sizes={sizes} results={ok}")
else:
    skip("动态 H/W 与尺寸自由度", "缺少前置权重资产")


# ---------------------------------------------------------------------------
section("7. 源码级检查（不得把开发机实测值写进产品）")

tool_src = TOOL.read_text(encoding="utf-8")
check("工具不含设备型号字面量（ADR-004 原则 2）",
      not [w for w in ("3050", "4090", "RTX ", "GTX ", "GeForce") if w in tool_src])
check("工具不把本机实测 tile 值当默认值", not re.search(r"tile\s*=\s*(?:256|512)\b", tool_src))
check("工具显式选择 TorchScript 导出路径并写明理由（dynamo 需额外 onnxscript）",
      "dynamo=False" in tool_src and "onnxscript" in tool_src)
check("工具对产物的写入是原子的（tempfile + os.replace）",
      "tempfile.mkstemp" in tool_src and "os.replace(" in tool_src)
check("服务层把环境缺失当状态而非异常（返回 install 指引）",
      "_INSTALL_HINT" in conv_svc_src and "convert_env_missing" in conv_svc_src)
check("服务层把产物临时文件交给 F-05 导入链搬移（不预先放到 imported/ 触发二次改名）",
      "primary_tmp=produced" in conv_svc_src and "imported_dir() / out_name" not in conv_svc_src)
check("服务层对外 id 与其它端点同形（mdl_ 前缀）",
      'f"mdl_{row.id}"' in conv_svc_src and 'f"mdl_{reused.id}"' in conv_svc_src)
check("服务层复用 F-05 导入链登记（不另写一套入库逻辑）",
      "model_registry.save_import(" in conv_svc_src)


# ---------------------------------------------------------------------------
print(f"\n===== 结果：{PASS} 通过 / {FAIL} 失败 / {SKIP} 跳过 =====")
shutil.rmtree(TMP, ignore_errors=True)
raise SystemExit(1 if FAIL else 0)
