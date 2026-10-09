"""模型加载器与格式路由（T-806；ADR-003 / PRD §7.5）。

把"磁盘上的模型文件"变成"可以 `infer()` 的后端对象"，并回答三个问题：
① 这个格式此刻能不能加载（运行时在不在）；② 加载它需要哪些配套文件；
③ 当两者都有问题时，先报哪一个（**答：先报制品残缺**，见 `load_backend` 的顺序说明）。

## 后端抽象

`InferenceBackend` 只暴露三件事：`infer(tensor) -> tensor` / `describe()` / `close()`。
M2 编排（`pipeline.py`）**不认识 ORT / OpenVINO / ncnn**，只按决策结果拿到一个后端对象——
换后端不会波及分块、拼接、进度与产物逻辑。

## 格式路由（ADR-003：确定性判定，**不猜**）

```
.onnx                 → ORT（CUDA / TensorRT / CPU EP）        ← S1 主路径（应用环境必备）
.xml + .bin           → OpenVINO 原生 API（**不走 ORT + OV EP**，实测负收益）
.param + .bin         → ncnn 运行时
.pth / .safetensors   → 拒绝加载 → 指向离线转换（T-807）
单独 .bin             → 拒绝（导入层已拦，这里再兜一次）
```

## 精度语义（**易误解，务必看清**）

`profile.precision` 是**决策**；模型文件的输入 dtype 是**事实**。本模块**始终按模型实际的
输入 dtype 喂数据**（fp16 导出的模型必须喂 fp16，否则 ORT 直接报类型不匹配），并把实际
dtype 经 `describe()["input_dtype"]` 如实报出。**"决策说 fp16、模型是 fp32"不做运行时精度
转换**（那需要改写整张图）——由执行器记一条显式说明，而不是假装跑的是 fp16。

## ncnn 的两个陷阱（T-210 实测踩出，勿"修回去"）

1. `ncnn.Mat(numpy)` 把三维数组解释为 **(c, h, w)**（不是 HWC），且**借用**缓冲区不拷贝；
   临时数组若在 `extract()` 前被 GC，进程**无回溯段错误**（exit 139）。
   → 本模块用局部变量持有 `(c,h,w)` 数据，直到 `extract()` 返回。
2. `create_gpu_instance()` 必须与 `destroy_gpu_instance()` 配对，否则解释器退出阶段段错误。
   → `NcnnBackend.close()` 负责配对，且**失败路径也要配对**。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np

from . import model_introspect, runtimes

logger = logging.getLogger("websr.engine.model_loader")

__all__ = [
    "ModelLoadError",
    "LoadSpec",
    "InferenceBackend",
    "FormatRoute",
    "FORMAT_ROUTES",
    "route_for",
    "load_backend",
]

# ---- 失败原因码（detail 里带着走，便于诊断导出与前端文案）----
RUNTIME_MISSING = "runtime_missing"
FILE_MISSING = "file_missing"
COMPANION_MISSING = "companion_missing"
UNSUPPORTED_FORMAT = "unsupported_format"
NEEDS_CONVERT = "needs_convert"
LOAD_FAILED = "load_failed"
INFERENCE_FAILED = "inference_failed"


class ModelLoadError(Exception):
    """模型加载/推理失败。`code` 是机器可判的稳定标识，`message` 面向用户。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        reason: str | None = None,
        detail: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.reason = reason
        self.detail = detail or {}

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "reason": self.reason,
            "detail": dict(self.detail),
        }


# ---------------------------------------------------------------------------
# 格式路由
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FormatRoute:
    """一种格式的加载路由（ADR-003 的代码表达）。"""

    fmt: str
    primary_ext: str
    companion_ext: str | None
    runtime: str | None  # None = 本应用不加载（需离线转换）
    loader: str  # ort | openvino | ncnn | none
    note: str


FORMAT_ROUTES: dict[str, FormatRoute] = {
    "onnx": FormatRoute("onnx", ".onnx", None, "onnxruntime", "ort",
                        "主路径：ORT 直接加载，零额外依赖"),
    "openvino_ir": FormatRoute("openvino_ir", ".xml", ".bin", "openvino", "openvino",
                               "OpenVINO 原生 API；不用 ORT + OpenVINO EP（实测慢 2.5 倍）"),
    "ncnn": FormatRoute("ncnn", ".param", ".bin", "ncnn", "ncnn",
                        "条件支持：具体模型的层覆盖需运行时校验，失败即剔除，不影响其它格式"),
    "pth": FormatRoute("pth", ".pth", None, None, "none",
                       "PyTorch 权重需离线转换为 .onnx（应用内不捆绑 PyTorch）"),
    "safetensors": FormatRoute("safetensors", ".safetensors", None, None, "none",
                               "safetensors 权重需离线转换为 .onnx（应用内不捆绑 PyTorch）"),
}


def route_for(fmt: str) -> FormatRoute:
    route = FORMAT_ROUTES.get(fmt)
    if route is None:
        raise ModelLoadError(
            UNSUPPORTED_FORMAT,
            f"不支持的模型格式: {fmt}",
            reason="请导入 .onnx / .xml(+.bin) / .param(+.bin) / .pth / .safetensors 模型",
            detail={"format": fmt, "supported": sorted(FORMAT_ROUTES)},
        )
    return route


@dataclass(frozen=True)
class LoadSpec:
    """一次加载请求。`backend` 为 ORT EP 名（来自阶段 D 的决策）。"""

    path: Path
    fmt: str
    companion: Path | None = None
    scale: int = 4
    precision: str = "fp32"
    backend: str = "CPUExecutionProvider"
    num_threads: int | None = None
    use_vulkan: bool = False  # 仅 ncnn 使用；默认关闭（无 Vulkan 设备的回退行为未验证）


@runtime_checkable
class InferenceBackend(Protocol):
    """推理后端最小契约。"""

    kind: str

    def infer(self, tensor: np.ndarray) -> np.ndarray:
        """`(1, C, H, W)` float32 `[0,1]` → `(1, C, H*scale, W*scale)` float32。"""
        ...

    def describe(self) -> dict: ...

    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# ORT 后端
# ---------------------------------------------------------------------------

def _ort_providers(backend: str) -> list:
    """构造 EP 列表。CUDA 的选项直接沿用 P0 实测踩出来的那组（勿改回默认）。

    `arena_extend_strategy` 必须 `kSameAsRequested`：ORT 默认的 `kNextPowerOfTwo`
    在 fp32 + 大 tile 下把 arena 向上取整到 2 的幂，实测让峰值显存冲到 ~98%；
    `cudnn_conv_algo_search` 用 HEURISTIC：EXHAUSTIVE 会为每个候选算法压测 cuDNN，
    单块 fp32 tile-512 因此慢到 33 s（同一形状换 HEURISTIC 快 20 倍）。
    """
    if backend == "CUDAExecutionProvider":
        return [
            ("CUDAExecutionProvider", {
                "device_id": 0,
                "cudnn_conv_algo_search": "HEURISTIC",
                "arena_extend_strategy": "kSameAsRequested",
                "do_copy_in_default_stream": "1",
            }),
            "CPUExecutionProvider",
        ]
    if backend == "TensorrtExecutionProvider":
        # TensorRT 属 G-04（T-909，P3）——此处只保证"给了就用、失败由 ORT 回退"
        return ["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


class OrtBackend:
    """`.onnx` 主路径后端。"""

    kind = "onnxruntime"

    def __init__(self, session: Any, input_spec: model_introspect.InputSpec,
                 *, requested_backend: str) -> None:
        self._sess = session
        self.input_spec = input_spec
        self._input_name = input_spec.name
        self._output_names = [o.name for o in session.get_outputs()]
        self._np_dtype = np.dtype(input_spec.dtype)
        self.requested_backend = requested_backend
        self.providers = list(session.get_providers())

    @classmethod
    def load(cls, spec: LoadSpec) -> "OrtBackend":
        try:
            import onnxruntime as ort
        except Exception as exc:
            raise ModelLoadError(
                RUNTIME_MISSING, "onnxruntime 运行时不可用，无法加载 .onnx 模型",
                reason=runtimes.status_for_format("onnx").reason,
                detail={"exception": f"{type(exc).__name__}: {exc}"},
            ) from exc

        so = ort.SessionOptions()
        so.log_severity_level = 3
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if spec.num_threads and spec.num_threads > 0:
            # CPU 档：线程数由运行时探测决定（不写死）；inter_op 固定 1（单图无并行分支收益）
            so.intra_op_num_threads = int(spec.num_threads)
            so.inter_op_num_threads = 1

        try:
            sess = ort.InferenceSession(
                str(spec.path), sess_options=so, providers=_ort_providers(spec.backend)
            )
        except Exception as exc:
            raise ModelLoadError(
                LOAD_FAILED, "模型加载失败",
                reason="请确认模型文件完整、且与所选后端兼容；如为 ncnn 模型请检查层覆盖",
                detail={"format": spec.fmt, "backend": spec.backend,
                        "exception": f"{type(exc).__name__}: {exc}"},
            ) from exc

        isp = model_introspect.spec_from_session(sess)
        return cls(sess, isp, requested_backend=spec.backend)

    def infer(self, tensor: np.ndarray) -> np.ndarray:
        if self._sess is None:
            raise ModelLoadError(INFERENCE_FAILED, "推理会话已关闭")
        x = np.ascontiguousarray(tensor).astype(self._np_dtype, copy=False)
        try:
            outs = self._sess.run(self._output_names, {self._input_name: x})
        except Exception as exc:
            raise ModelLoadError(
                INFERENCE_FAILED, "推理执行失败",
                reason="模型可能在当前显存/内存下无法完成该尺寸的推理",
                detail={"exception": f"{type(exc).__name__}: {exc}"},
            ) from exc
        return np.asarray(outs[0])

    def describe(self) -> dict:
        return {
            "kind": self.kind,
            "providers": list(self.providers),
            "requested_backend": self.requested_backend,
            "input_dtype": self.input_spec.dtype,
            "input_spec": self.input_spec.to_dict(),
            "precision_effective": "fp16" if self._np_dtype == np.dtype("float16") else "fp32",
        }

    def close(self) -> None:
        self._sess = None


# ---------------------------------------------------------------------------
# OpenVINO 后端
# ---------------------------------------------------------------------------

class OpenVinoBackend:
    """OpenVINO IR（`.xml` + `.bin`）后端——**原生 API，不经 ORT EP**。"""

    kind = "openvino"

    def __init__(self, compiled: Any, xml_path: Path, *, device: str) -> None:
        self._compiled = compiled
        self._req = compiled.create_infer_request()
        self._in_port = compiled.input(0)
        self._out_index = 0
        self.xml_path = xml_path
        self.device = device

    @classmethod
    def load(cls, spec: LoadSpec) -> "OpenVinoBackend":
        if spec.companion is None:
            raise ModelLoadError(
                COMPANION_MISSING, "OpenVINO IR 必须同时提供 .xml 与 .bin",
                reason="请在导入时把 .bin 作为配套文件一起选择",
            )
        try:
            import openvino as ov
        except Exception as exc:
            raise ModelLoadError(
                RUNTIME_MISSING, "OpenVINO 运行时未安装，无法加载 IR 模型",
                reason=runtimes.status_for_format("openvino_ir").reason,
                detail={"exception": f"{type(exc).__name__}: {exc}"},
            ) from exc

        device = "CPU"  # Intel 原生路径；具体设备由后续能力的 OpenVINO 探测决定（S3）
        try:
            core = ov.Core()
            model = core.read_model(str(spec.path))
            compiled = core.compile_model(model, device)
        except Exception as exc:
            raise ModelLoadError(
                LOAD_FAILED, "OpenVINO 模型加载失败",
                reason="请确认 .xml 与 .bin 配套且未被截断",
                detail={"device": device, "exception": f"{type(exc).__name__}: {exc}"},
            ) from exc
        return cls(compiled, spec.path, device=device)

    def infer(self, tensor: np.ndarray) -> np.ndarray:
        arr = np.ascontiguousarray(tensor, dtype=np.float32)
        try:
            self._req.infer({self._in_port: arr})
            out = self._req.get_output_tensor(self._out_index).data
        except Exception as exc:
            raise ModelLoadError(
                INFERENCE_FAILED, "OpenVINO 推理执行失败",
                detail={"exception": f"{type(exc).__name__}: {exc}"},
            ) from exc
        return np.asarray(out)

    def describe(self) -> dict:
        return {
            "kind": self.kind,
            "device": self.device,
            "input_dtype": "float32",
            "precision_effective": "fp32",  # 入参统一 float32；IR 内部精度由 OpenVINO 决定
            "note": "OpenVINO 原生 API（非 ORT EP）：实测 ORT + OpenVINO EP 为负收益",
        }

    def close(self) -> None:
        self._req = None
        self._compiled = None


# ---------------------------------------------------------------------------
# ncnn 后端
# ---------------------------------------------------------------------------

def parse_ncnn_param(path: Path) -> dict:
    """读 ncnn `.param` 的输入/输出 blob 名。

    ncnn 的 `.param` **不标记输出**：产物里"被生产但从未被消费"的 blob 才是输出。
    产品实现（`tools/` 下的同名解析器是开发期基准，产品代码不得 import）。
    """
    try:
        lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError as exc:
        raise ModelLoadError(FILE_MISSING, "无法读取 ncnn .param 文件",
                             detail={"exception": str(exc)}) from exc
    if not lines or not lines[0].startswith("7767517"):
        raise ModelLoadError(UNSUPPORTED_FORMAT, "不是有效的 ncnn .param 文件",
                             reason="请确认选择的是 ncnn 模型（.param 首行为 7767517）")

    produced: list[str] = []
    consumed: set[str] = set()
    inputs: list[str] = []
    for raw in lines[2:]:
        f = raw.split()
        if len(f) < 4:
            continue
        typ = f[0]
        try:
            n_in, n_out = int(f[2]), int(f[3])
        except ValueError:
            continue
        consumed.update(f[4:4 + n_in])
        produced.extend(f[4 + n_in:4 + n_in + n_out])
        if typ == "Input":
            inputs.extend(f[4 + n_in:4 + n_in + n_out])

    dangling = [b for b in produced if b not in consumed]
    preferred = [b for b in dangling if "output" in b.lower()]
    return {"inputs": inputs or ["data"], "outputs": preferred or dangling or ["output"]}


class NcnnBackend:
    """ncnn（`.param` + `.bin`）后端——可选后端，加载失败即剔除。"""

    kind = "ncnn"

    def __init__(self, net: Any, in_name: str, out_name: str, *,
                 use_vulkan: bool, gpu_instance: bool) -> None:
        self._net = net
        self._in_name = in_name
        self._out_name = out_name
        self.use_vulkan = use_vulkan
        self._gpu_instance = gpu_instance

    @classmethod
    def load(cls, spec: LoadSpec) -> "NcnnBackend":
        if spec.companion is None:
            raise ModelLoadError(
                COMPANION_MISSING, "ncnn 模型必须同时提供 .param 与 .bin",
                reason="请在导入时把 .bin 作为配套文件一起选择",
            )
        try:
            import ncnn
        except Exception as exc:
            raise ModelLoadError(
                RUNTIME_MISSING, "ncnn 运行时未安装，无法加载 ncnn 模型",
                reason=runtimes.status_for_format("ncnn").reason,
                detail={"exception": f"{type(exc).__name__}: {exc}"},
            ) from exc

        meta = parse_ncnn_param(spec.path)
        in_name = meta["inputs"][0]
        out_name = meta["outputs"][-1]

        gpu_instance = False
        if spec.use_vulkan:
            try:
                ncnn.create_gpu_instance()  # 必须与 destroy_gpu_instance 配对（T-210 陷阱 2）
                gpu_instance = True
            except Exception as exc:
                logger.warning("ncnn Vulkan 初始化失败，回落 CPU：%s", exc)
                gpu_instance = False

        net = ncnn.Net()
        net.opt.use_vulkan_compute = bool(gpu_instance)
        if spec.num_threads and spec.num_threads > 0:
            net.opt.num_threads = int(spec.num_threads)

        try:
            rc_p = net.load_param(str(spec.path))
            rc_m = net.load_model(str(spec.companion))
        except Exception as exc:
            if gpu_instance:
                cls._destroy_gpu(ncnn)
            raise ModelLoadError(
                LOAD_FAILED, "ncnn 模型加载失败",
                reason="ncnn 的层覆盖弱于 ONNX 运行时；该模型可能含未实现的层",
                detail={"exception": f"{type(exc).__name__}: {exc}"},
            ) from exc
        if rc_p != 0 or rc_m != 0:
            if gpu_instance:
                cls._destroy_gpu(ncnn)
            raise ModelLoadError(
                LOAD_FAILED, "ncnn 模型加载失败",
                reason="ncnn 的层覆盖弱于 ONNX 运行时；该模型可能含未实现的层",
                detail={"param_rc": int(rc_p), "model_rc": int(rc_m)},
            )

        return cls(net, in_name, out_name, use_vulkan=bool(gpu_instance), gpu_instance=gpu_instance)

    @staticmethod
    def _destroy_gpu(ncnn_mod: Any) -> None:
        try:
            ncnn_mod.destroy_gpu_instance()
        except Exception:  # 已在别处销毁或 Vulkan 不可用
            pass

    def infer(self, tensor: np.ndarray) -> np.ndarray:
        if self._net is None:
            raise ModelLoadError(INFERENCE_FAILED, "推理会话已关闭")
        import ncnn

        # 陷阱 1：Mat 按 (c,h,w) 解释且**借用**缓冲区 → 用局部变量持有到 extract 之后
        data = np.ascontiguousarray(tensor[0], dtype=np.float32)
        mat_in = ncnn.Mat(data)
        try:
            ex = self._net.create_extractor()
            rc_in = ex.input(self._in_name, mat_in)
            if rc_in != 0:
                raise ModelLoadError(INFERENCE_FAILED, "ncnn 输入绑定失败",
                                     detail={"rc": int(rc_in), "blob": self._in_name})
            rc, out = ex.extract(self._out_name)
            if rc != 0:
                raise ModelLoadError(INFERENCE_FAILED, "ncnn 推理执行失败",
                                     reason="该模型的层覆盖可能不完整",
                                     detail={"rc": int(rc), "blob": self._out_name})
            arr = np.array(out, dtype=np.float32)  # (c,h,w) 的真实拷贝
        finally:
            # 显式按住 data 的引用直到 extract 完成，再一并释放
            del mat_in, data
        return arr[None, ...]

    def describe(self) -> dict:
        return {
            "kind": self.kind,
            "use_vulkan": self.use_vulkan,
            "input_blob": self._in_name,
            "output_blob": self._out_name,
            "input_dtype": "float32",
            "precision_effective": "fp32",
            "note": "ncnn 为可选后端；层覆盖不足时加载/推理会失败并被剔除",
        }

    def close(self) -> None:
        self._net = None
        if self._gpu_instance:
            try:
                import ncnn

                self._destroy_gpu(ncnn)
            finally:
                self._gpu_instance = False


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------

_LOADERS = {
    "ort": OrtBackend,
    "openvino": OpenVinoBackend,
    "ncnn": NcnnBackend,
}


def load_backend(spec: LoadSpec) -> InferenceBackend:
    """按格式路由加载模型。**失败一律抛 `ModelLoadError`（含可判别的 code）。**

    ## 校验顺序：先问「制品完不完整」，再问「本机跑不跑得动」

    同一份残缺的模型，在任何机器上都应该得到**同一个错误码**——所以结构性校验
    （文件在不在 / 配套齐不齐）排在环境性校验（运行时装没装）之前：

    - 若先报 `runtime_missing`，一台没装 openvino 的机器上永远看不到"缺 `.bin` 配套"，
      而那才是用户真正要修的问题（装上运行时后依旧失败，白折腾一轮）；
    - 更实际的是：应用环境只装 CPU 版 onnxruntime，运行时前置会让 companion 分支
      **在应用环境不可观测**，只能靠跨环境探针间接证明。

    因此顺序为：`needs_convert` → `file_missing` → `companion_missing` → `runtime_missing`。

    注：`runtime_missing` 的 `detail.optional=True`（可选后端缺失，属软提示）与
    `companion_missing`（制品残缺，硬错误）语义不同，不该互相掩盖。
    """
    route = route_for(spec.fmt)

    if route.runtime is None:
        raise ModelLoadError(
            NEEDS_CONVERT,
            f"{spec.fmt} 格式不能直接加载",
            reason=route.note,
            detail={"format": spec.fmt, "action": "convert_to_onnx"},
        )

    # ---- 结构性校验：与环境无关，同一残缺制品在任何机器上同码 ----
    primary = Path(spec.path)
    if not primary.is_file():
        raise ModelLoadError(FILE_MISSING, "模型文件不存在或已被移动",
                             detail={"path": str(primary)})
    if route.companion_ext is not None:
        if spec.companion is None:
            raise ModelLoadError(
                COMPANION_MISSING, f"{spec.fmt} 格式必须同时提供 {route.companion_ext} 配套文件",
                reason="导入时应把配套文件一起选择（ADR-003：靠配套文件分派，不靠文件名猜）",
                detail={"format": spec.fmt, "companion_ext": route.companion_ext},
            )
        companion = Path(spec.companion)
        if not companion.is_file():
            raise ModelLoadError(COMPANION_MISSING, "配套文件不存在或已被移动",
                                 detail={"path": str(companion)})
    elif primary.suffix.lower() == ".bin":
        raise ModelLoadError(
            COMPANION_MISSING, ".bin 是权重体，不能单独作为主文件加载",
            reason="必须与 .xml（OpenVINO IR）或 .param（ncnn）一同提供",
        )

    # ---- 环境性校验：制品完整，才轮到问本机能不能跑 ----
    status = runtimes.status_for_module(route.runtime)
    if not status.available:
        raise ModelLoadError(
            RUNTIME_MISSING,
            f"{spec.fmt} 格式所需的运行时「{route.runtime}」未安装",
            reason=status.reason,
            detail={"format": spec.fmt, "runtime": route.runtime, "optional": status.optional},
        )

    return _LOADERS[route.loader].load(spec)
