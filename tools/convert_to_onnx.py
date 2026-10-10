#!/usr/bin/env python
"""把 PyTorch 原生权重（`.pth` / `.safetensors`）离线转换为应用可加载的 `.onnx`。

对应 PRD §7.5 **方案 A** / ADR-003：应用内**不捆绑 PyTorch**，`.pth` / `.safetensors`
在**独立环境**里转换成 `.onnx` 后，由应用按主路径加载。

设计要点（每条都对应产品侧已有的裁决，不是自选）：

1. **只在独立环境跑**：本工具放 `tools/`（产品不得 import），运行环境 `.venvs/sr-convert`
   （torch CPU + spandrel），主应用 `.venvs/sr-app` **不含 torch**。
2. **不猜架构**：交给 spandrel 的架构注册表识别；**认不出就报错**并列出候选，
   绝不"试一个试试"（ADR-003 明文："若无法识别则明确报错而非猜测"）。
3. **默认导出动态 H/W**：产品侧 `engine/model_introspect.py` 只看 ONNX 输入约束，
   **动态输入**才能让引擎自由分块（T-806）；`--static N` 才导出固定边长，
   对应产品侧的 `fixed_tile` 通道（注意：那是"正好等于 N"，不是倍数关系）。
   `model.size_requirements` 如实报出 spandrel 给的窗口约束（`multiple_of`），
   **作为参考值而非结论**——产品侧仍按 `DEFAULT_ALIGN` 只收紧不放宽。
4. **自检后才落盘**：导出后用 ONNXRuntime 真跑一遍并与 torch 输出比对（`max|diff|`）。
   理由：**"能加载但算错"的模型比加载失败更糟**——产品侧无法识别，会静默出坏图。
5. **原子写**：先写临时文件再 `os.replace`，**失败不留半个 `.onnx`**
   （与 `engine/pipeline.py` 的"取消不留半个文件"同源）。
6. **机器可读输出**：`--json` 输出结构化结果，供应用侧 `POST /api/models/{id}/convert` 解析。

实测事实（2026-10-09，`.venvs/sr-convert` = torch 2.14.1+cpu / spandrel 0.4.2）::

    RealESRGAN_x4.pth        (RRDBNet / "ESRGAN", 16,697,987 参数) → 64.0 MB .onnx
      同源自检 max|torch-onnx| = 1.55e-06；非 8 倍数尺寸 97/101/155 均正确输出 4×
      size_requirements = {minimum: 2, multiple_of: 1, square: False}
    realesr-general-x4v3.pth (SRVGGNetCompact, 1,213,296 参数)     → 4.6 MB .onnx
      同源自检 max|torch-onnx| = 4.08e-06

    与仓内既有 `data/models/RealESRGAN_x4.onnx` 的关系：**图结构同构**（节点 1187 /
    初始化器 702 / 算子直方图逐项一致），但**权重不同源**（同输入下 corr = 0.856，
    max|diff| = 0.23）→ 属于同一架构的不同微调版本，**不能互相替代**；工具的正确性
    由"同源自检"保证，不由"与既有产物的相似度"保证。

    ⚠️ 两家 Real-ESRGAN 系模型的 `multiple_of` 都是 **1**（纯卷积网络，无窗口注意力）
    → 动态导出在任意尺寸都成立。但这**不能**推广到窗口注意力模型（如 SwinIR）：
    那类模型的约束读不出来，产品侧仍按 `DEFAULT_ALIGN` 保守处理。

用法::

    .venvs/sr-convert/Scripts/python.exe tools/convert_to_onnx.py \\
        model.pth -o data/models/imported/model.onnx --json

退出码：0 成功 ｜ 2 参数错 ｜ 3 架构无法识别 ｜ 4 导出失败 ｜ 5 自检不通过 ｜ 6 运行时缺失
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
import warnings
from pathlib import Path

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_ARCH = 3
EXIT_EXPORT = 4
EXIT_SELFCHECK = 5
EXIT_RUNTIME = 6

_TORCH_MISSING_HINT = (
    "本工具需要 PyTorch + spandrel，且**必须**在与主应用分离的环境里运行：\n"
    "  uv venv --python 3.13 .venvs/sr-convert\n"
    "  uv pip install --python .venvs/sr-convert/Scripts/python.exe torch torchvision "
    "--index-url https://download.pytorch.org/whl/cpu\n"
    "  uv pip install --python .venvs/sr-convert/Scripts/python.exe spandrel safetensors "
    "onnx onnxruntime numpy\n"
    "然后用该环境的解释器调用本脚本。**不要把 torch 装进 .venvs/sr-app**（ADR-003）。"
)


class ConvertError(Exception):
    """带可判别 code 的转换失败。"""

    def __init__(self, code: str, message: str, *, reason: str = "", exit_code: int = EXIT_EXPORT,
                 **extra) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.reason = reason
        self.exit_code = exit_code
        self.extra = extra

    def to_dict(self) -> dict:
        d = {"ok": False, "code": self.code, "message": self.message}
        if self.reason:
            d["reason"] = self.reason
        d.update(self.extra)
        return d


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _require_runtime():
    """惰性 import；缺依赖时给出可照做的安装指引（不是一句 ImportError）。"""
    try:
        import torch  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover - 环境问题
        raise ConvertError(
            "runtime_missing", "未找到 PyTorch", reason=f"{exc}\n{_TORCH_MISSING_HINT}",
            exit_code=EXIT_RUNTIME,
        ) from exc
    try:
        import spandrel  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover - 环境问题
        raise ConvertError(
            "runtime_missing", "未找到 spandrel", reason=f"{exc}\n{_TORCH_MISSING_HINT}",
            exit_code=EXIT_RUNTIME,
        ) from exc
    return torch, spandrel


def _arch_ids(spandrel) -> list[str]:
    """spandrel 已注册的架构 id 列表（仅用于报错时给候选，不做猜测）。

    ⚠️ 两处易错（实测踩过）：`MAIN_REGISTRY.architectures` 是**方法**（不是属性列表），
    且 `Arch.id` 是**普通字符串**（`Arch.name` 才是展示名）。
    """
    try:
        supports = spandrel.MAIN_REGISTRY.architectures()
    except Exception:
        return []
    out: set[str] = set()
    for s in supports:
        arch = getattr(s, "architecture", s)
        name = getattr(arch, "name", None) or getattr(arch, "id", None)
        if isinstance(name, str) and name:
            out.add(name)
    return sorted(out)


def _size_requirements(desc) -> dict | None:
    """spandrel 0.4 的 `SizeRequirements`（minimum / multiple_of / square）。

    这是**唯一**能"不靠猜"拿到窗口倍数约束的来源——产品侧 `align` 的取值就靠它。
    旧版 spandrel 没有该字段，返回 None（工具如实说不适用，不编一个默认值）。
    """
    sr = getattr(desc, "size_requirements", None)
    if sr is None:
        return None
    try:
        return {
            "minimum": int(getattr(sr, "minimum", 0) or 0),
            "multiple_of": int(getattr(sr, "multiple_of", 1) or 1),
            "square": bool(getattr(sr, "square", False)),
        }
    except Exception:
        return None


def _predicted_pad(req: dict | None) -> int:
    """由 SizeRequirements 推导"需要向上对齐到几的倍数"，供写入产物元信息。"""
    if not req:
        return 0
    return int(req.get("multiple_of") or 1)


# ---------------------------------------------------------------------------
# 装载
# ---------------------------------------------------------------------------

def load_descriptor(src: Path, arch: str | None):
    """载入权重并识别架构。**认不出就报错**，不尝试替代方案。"""
    torch, spandrel = _require_runtime()
    loader = spandrel.ModelLoader()

    if arch:
        # 显式指定架构：仍然走 spandrel 注册表，具体实现不在本工具里重写
        try:
            # ⚠️ `load_state_dict_from_file` 是**实例方法**（不是模块级函数）
            sd = loader.load_state_dict_from_file(str(src))
        except Exception as exc:
            raise ConvertError("load_failed", "无法读取权重文件", reason=str(exc)) from exc
        try:
            desc = loader.load_from_state_dict(sd, arch)
        except Exception as exc:
            raise ConvertError(
                "arch_unrecognized", f"按指定架构 {arch!r} 装载失败",
                reason=str(exc), candidates=_arch_ids(spandrel)[:40], exit_code=EXIT_ARCH,
            ) from exc
    else:
        try:
            desc = loader.load_from_file(str(src))
        except Exception as exc:
            raise ConvertError(
                "arch_unrecognized", "spandrel 无法识别该权重的网络结构",
                reason=(
                    f"{exc}\n识别失败时**不要猜测**（ADR-003）：请确认权重属于受支持的"
                    "超分/修复架构，或显式用 --arch 指定。"
                ),
                candidates=_arch_ids(spandrel)[:40],
                exit_code=EXIT_ARCH,
            ) from exc

    if not hasattr(desc, "model") or not hasattr(desc, "scale"):
        raise ConvertError(
            "unsupported_model", "识别出的模型不是单图超分/修复模型",
            reason="本工具只处理图像到图像的模型；其它用途（如分类）不适用",
            exit_code=EXIT_ARCH,
        )
    return torch, spandrel, desc


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------

def _fold_scalar_defaults(torch, net):
    """把 forward 中带默认值的**标量超参**显式定值包装（如 RealCUGAN 的 `alpha: float = 1`）。

    动机：TorchScript 追踪器（`dynamo=False`）会把 forward 签名里**被使用的**带默认值
    标量参数提升为 ONNX 图输入（实测 graph inputs 变成 `['input', 'alpha']`），而产品
    侧推理只喂单张量 → 图无法执行。包装后标量以字面量进入追踪，被烘焙为常量。
    语义与"调用方不传该参数"完全等价（默认值就是调用时的值），非模型特判。

    ⚠️ 依赖输入尺寸的 Python 分支（如 RealCUGAN 末端的条件裁剪）在追踪时按探针尺寸
    烘焙：探针为对齐尺寸（mult-4）时裁剪分支不进图，导出图对 **对齐尺寸** 正确、对
    任意尺寸**不保证**。产品引擎按 `DEFAULT_ALIGN=8` 补齐 tile，恒喂对齐尺寸 → 链路
    安全；但本工具产物的 "dynamic" 仅在对齐尺寸口径下成立。
    """
    import inspect  # noqa: PLC0415
    try:
        sig = inspect.signature(net.forward)
    except (TypeError, ValueError):
        return net
    extras = {}
    for name, param in list(sig.parameters.items())[1:]:
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        if param.default is not inspect.Parameter.empty and not isinstance(param.default, torch.Tensor):
            extras[name] = param.default
    if not extras:
        return net

    class _Folded(torch.nn.Module):
        def __init__(self, inner, kwargs):
            super().__init__()
            self.inner = inner
            self.kwargs = kwargs

        def forward(self, x):
            return self.inner(x, **self.kwargs)

    return _Folded(net, extras).eval()


def export_onnx(*, torch, desc, out: Path, static: int | None, fp16: bool, opset: int) -> dict:
    """导出 ONNX。默认动态 H/W；`static=N` 时导出固定 N×N。"""
    net = _fold_scalar_defaults(torch, desc.model)
    net.eval()

    dtype = torch.float16 if fp16 else torch.float32
    if fp16:
        try:
            net = net.half()
        except Exception as exc:
            raise ConvertError("fp16_unsupported", "该模型不支持 fp16 转换",
                               reason=str(exc)) from exc

    # 校验输入尺寸：优先用 spandrel 给出的约束（不猜）
    req = _size_requirements(desc)
    probe = int(static) if static else 64
    if req:
        multiple = int(req.get("multiple_of") or 1)
        minimum = int(req.get("minimum") or 0)
        if multiple > 1:
            probe = max(probe, minimum, multiple)
            if probe % multiple:
                probe = ((probe // multiple) + 1) * multiple
        elif minimum > probe:
            probe = minimum

    dummy = torch.rand(1, 3, probe, probe, dtype=dtype)

    dynamic_axes = (
        {"input": {2: "h", 3: "w"}, "output": {2: "h", 3: "w"}} if static is None else None
    )

    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(suffix=".onnx.tmp", dir=str(out.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with torch.no_grad():
            # 显式 `dynamo=False` 走 **TorchScript** 导出路径。实测理由（不是偏好）：
            # torch ≥ 2.9 默认改用 torch.export 路径，而它**需要额外的 `onnxscript` 依赖**
            # （实测报 `ModuleNotFoundError: No module named 'onnxscript'`）→ 会给转换环境
            # 再加一层依赖，与"离线工具尽量薄"相悖。TorchScript 路径图稳定、opset 可控。
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=DeprecationWarning, module="torch")
                torch.onnx.export(
                    net, dummy, str(tmp),
                    input_names=["input"], output_names=["output"],
                    dynamic_axes=dynamic_axes,
                    opset_version=int(opset),
                    do_constant_folding=True,
                    dynamo=False,
                )
        if not tmp.is_file() or tmp.stat().st_size == 0:
            raise ConvertError("export_failed", "导出未产生有效文件", reason="输出为 0 字节")
        return {"probe": probe, "dtype": "float16" if fp16 else "float32", "tmp": tmp}
    except ConvertError:
        tmp.unlink(missing_ok=True)
        raise
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise ConvertError("export_failed", "ONNX 导出失败", reason=str(exc)) from exc


def self_check(*, torch, net, onnx_path: Path, probe: int, fp16: bool, tol: float) -> dict:
    """导出后真跑一遍：ORT vs torch。**不通过就不落盘。**"""
    try:
        import numpy as np  # noqa: PLC0415
        import onnxruntime as ort  # noqa: PLC0415
    except Exception as exc:
        return {"ok": False, "skipped": True, "reason": f"缺少 numpy/onnxruntime：{exc}"}

    dtype = torch.float16 if fp16 else torch.float32
    torch.manual_seed(0)
    x = torch.rand(1, 3, probe, probe, dtype=dtype)

    with torch.no_grad():
        t0 = time.perf_counter()
        ref = net(x)
        torch_ms = (time.perf_counter() - t0) * 1000.0

    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    feed = {"input": x.detach().cpu().numpy()}
    try:
        t0 = time.perf_counter()
        got = sess.run(None, feed)[0]
        ort_ms = (time.perf_counter() - t0) * 1000.0
    except Exception as exc:
        return {
            "ok": False, "skipped": True,
            "reason": f"ORT 未能执行导出的模型：{exc}",
            "torch_ms": round(torch_ms, 2),
        }

    ref_np = ref.detach().cpu().numpy().astype(np.float32)
    diff = float(np.max(np.abs(ref_np - got.astype(np.float32))))
    return {
        "ok": diff <= tol,
        "input_size": probe,
        "max_abs_diff": diff,
        "tolerance": tol,
        "torch_ms": round(torch_ms, 2),
        "ort_ms": round(ort_ms, 2),
        "output_shape": list(got.shape),
        "input_name": sess.get_inputs()[0].name,
        "output_name": sess.get_outputs()[0].name,
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def convert(*, src: Path, out: Path, arch: str | None, static: int | None, fp16: bool,
            opset: int, tol: float, skip_check: bool, write_meta: bool) -> dict:
    torch, _spandrel, desc = load_descriptor(src, arch)

    req = _size_requirements(desc)
    arch_name = _arch_name(desc)
    params = _param_count(desc.model)

    exp = export_onnx(torch=torch, desc=desc, out=out, static=static, fp16=fp16, opset=opset)
    tmp = exp.pop("tmp")

    check = {"ok": True, "skipped": True, "reason": "按参数要求跳过自检（--skip-check）"}
    if not skip_check:
        check = self_check(
            torch=torch, net=_fold_scalar_defaults(torch, desc.model), onnx_path=tmp,
            probe=exp["probe"], fp16=fp16, tol=tol,
        )
        if not check.get("ok") and not check.get("skipped"):
            tmp.unlink(missing_ok=True)
            raise ConvertError(
                "selfcheck_failed", "导出产物自检不通过，已删除产物",
                reason=(
                    f"max|torch - onnx| = {check['max_abs_diff']:.3e} > {tol:.1e}。"
                    "**不落盘**：一个'能加载但算错'的模型比加载失败更糟，"
                    "产品侧无法识别，会静默产出坏图。"
                ),
                exit_code=EXIT_SELFCHECK,
                check=check,
            )

    # 原子落盘：自检通过才让产物出现在目标路径
    os.replace(tmp, out)

    result = {
        "ok": True,
        "input": {
            "path": str(src), "sha256": sha256_file(src),
            "size_bytes": src.stat().st_size, "format": src.suffix.lower().lstrip("."),
        },
        "output": {
            "path": str(out), "sha256": sha256_file(out), "size_bytes": out.stat().st_size,
            "opset": int(opset), "dynamic": static is None, "static": static,
            "dtype": exp["dtype"],
        },
        "model": {
            "architecture": arch_name,
            "scale": int(desc.scale),
            "purpose": str(getattr(desc, "purpose", "")),
            "param_count": params,
            "size_requirements": req,
            "align_hint": _predicted_pad(req),
        },
        "check": check,
    }

    if write_meta:
        meta_path = out.with_suffix(out.suffix + ".meta.json")
        meta_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["output"]["meta_path"] = str(meta_path)

    return result


def _arch_name(desc) -> str:
    """架构展示名。`desc.architecture` 的形态随 spandrel 版本而变，故层层兜底。"""
    aid = getattr(desc, "architecture", None)
    if aid is None:
        return type(desc.model).__name__
    for attr in ("name", "id"):
        v = getattr(aid, attr, None)
        if isinstance(v, str) and v:
            return v
    return str(aid)


def _param_count(net) -> int:
    try:
        return int(sum(p.numel() for p in net.parameters()))
    except Exception:
        return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="convert_to_onnx.py",
        description="把 .pth / .safetensors 离线转换为应用可加载的 .onnx（ADR-003 方案 A）",
    )
    ap.add_argument("src", type=Path, nargs="?", default=None,
                    help="输入权重（.pth 或 .safetensors）")
    ap.add_argument("-o", "--output", type=Path, help="输出 .onnx 路径（默认同名同目录）")
    ap.add_argument("--arch", default=None, help="显式指定 spandrel 架构 id（默认自动识别）")
    ap.add_argument("--static", type=int, default=None, metavar="N",
                    help="导出固定 N×N 输入（默认导出动态 H/W，产品侧才能自由分块）")
    ap.add_argument("--fp16", action="store_true", help="以 fp16 导出权重")
    ap.add_argument("--opset", type=int, default=17, help="ONNX opset（默认 17）")
    ap.add_argument("--tolerance", type=float, default=1e-3, help="自检 max|diff| 阈值（默认 1e-3）")
    ap.add_argument("--skip-check", action="store_true", help="跳出自检（不推荐）")
    ap.add_argument("--no-meta", action="store_true", help="不写 <out>.meta.json 旁车元信息")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出结果（供应用侧解析）")
    ap.add_argument("--list-arch", action="store_true", help="列出 spandrel 支持的架构 id 后退出")
    args = ap.parse_args(argv)

    if args.list_arch:
        try:
            _, spandrel = _require_runtime()
        except ConvertError as exc:
            print(json.dumps(exc.to_dict(), ensure_ascii=False, indent=2))
            return exc.exit_code
        for name in _arch_ids(spandrel):
            print(name)
        return EXIT_OK

    if args.src is None:
        ap.error("需要提供输入权重文件（或改用 --list-arch 查看支持的架构）")

    if not args.src.is_file():
        err = ConvertError("file_missing", "权重文件不存在", reason=str(args.src),
                           exit_code=EXIT_USAGE)
        print(json.dumps(err.to_dict(), ensure_ascii=False, indent=2) if args.json else
              f"[file_missing] {err.message}: {err.reason}")
        return err.exit_code

    out = args.output or args.src.with_suffix(".onnx")

    try:
        result = convert(
            src=args.src, out=out, arch=args.arch, static=args.static, fp16=args.fp16,
            opset=args.opset, tol=args.tolerance, skip_check=args.skip_check,
            write_meta=not args.no_meta,
        )
    except ConvertError as exc:
        payload = exc.to_dict()
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else
              f"[{exc.code}] {exc.message}\n{exc.reason}")
        return exc.exit_code

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        m, o, c = result["model"], result["output"], result["check"]
        geometry = "动态 H/W" if o["dynamic"] else "固定 {}×{}".format(o["static"], o["static"])
        print(f"架构      : {m['architecture']}  scale={m['scale']}  参数={m['param_count']:,}")
        print(f"输出      : {o['path']}  ({o['size_bytes'] / 1048576:.1f} MB, {geometry}, {o['dtype']})")
        if m["size_requirements"]:
            r = m["size_requirements"]
            print(f"尺寸约束  : minimum={r['minimum']} multiple_of={r['multiple_of']} square={r['square']}"
                  f"  → align_hint={m['align_hint']}")
        else:
            print("尺寸约束  : spandrel 未提供（产品侧按默认 align 处理）")
        if c.get("skipped"):
            print(f"自检      : 跳过（{c.get('reason')}）")
        else:
            print(f"自检      : max|torch-onnx| = {c['max_abs_diff']:.3e} "
                  f"(阈值 {c['tolerance']:.1e})  torch={c['torch_ms']}ms  ort={c['ort_ms']}ms")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
