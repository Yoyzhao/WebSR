"""ONNX 输入约束读取（T-806；兑现 T-804 挂账的「`align` 真实取值待 T-806 读 ONNX 约束」）。

阶段 D 需要知道"模型的输入张量长什么样"，才能决定 `tile` 的对齐倍数。T-804 当时
只能给保守默认值 8 并注明挂账，本模块把那个洞补上。

## 为什么用 onnxruntime 而不是 `onnx` 包

`onnx` 包**不在应用环境**（`.venvs/sr-app`）里，而 ORT 的 `InferenceSession` 已经解析过
整张图，能直接给出输入名 / shape / dtype / `metadata_props`——**不需要多引入一个依赖**。
代价是必须建一次 session；因此本模块对结果做**进程内缓存**（键含 size + mtime，
文件被替换即失效），每个模型每个进程只付一次代价。

## 关键判定：`fixed_tile` 与 `align`

- **静态输入边长**（如某基准变体固定 512×512）→ `fixed_tile = 512`：
  `tile` **必须正好等于它**，否则 ORT 直接报尺寸不匹配。这是必须读取硬约束。
- **动态 H/W**（RealESRGAN 主路径即如此）→ shape 里是符号维度，**读不出窗口约束**
  （SwinIR 类要求 8 的倍数，纯卷积的 RRDBNet 无约束，两者 shape 长得一样）。
  此时返回 `align = 1`（**含义是"未检测到约束"，不是"没有约束"**）并说明原因——不猜。

## 为什么 `align = 1` 不能直接拿去放宽分块

`align = 1` 的真实语义是**未知**，而不是**无约束**：一个动态导出、但内部有窗口注意力
（window=8/16）的模型同样会呈现 `[1, 3, "h", "w"]`。若据此把 `align` 从保守默认值 8
降到 1，就会把一个本来安全的 `tile` 变成非法尺寸。因此**本模块只能收紧、不能放宽**：
调用方（`services/engine_decision.py`）的取值口径是

```
effective_align = fixed_tile（静态硬约束，必须精确满足）或 max(DEFAULT_ALIGN, align)
```

即"读到硬约束就服从它，读不到就保留保守下界"。

**永不抛异常**：读不到就返回 `source="unknown"` 的保守结果（同阶段 A 的纪律）。
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("websr.engine.model_introspect")

__all__ = ["InputSpec", "spec_from_session", "spec_for_path", "clear_cache"]

#: ORT 的 dtype 字符串（`tensor(float)`）→ numpy dtype 名
_ORT_TYPE_TO_NUMPY: dict[str, str] = {
    "tensor(float)": "float32",
    "tensor(float16)": "float16",
    "tensor(double)": "float64",
    "tensor(uint8)": "uint8",
}

_DYNAMIC = "dynamic"
_STATIC = "static"
_UNKNOWN = "unknown"


@dataclass(frozen=True)
class InputSpec:
    """一个 ONNX 模型的输入契约（只关心**第一路输入**——超分模型均为单输入）。"""

    name: str
    dtype: str  # numpy dtype 名（'float32' / 'float16' / …）
    dims: tuple[Any, ...]  # 原始 shape（符号维度保持为 str）
    dynamic_hw: bool
    fixed_hw: tuple[int, int] | None  # 静态且 H==W 时的 (h, w)
    channels: int | None
    metadata: dict[str, str] = field(default_factory=dict)
    source: str = _UNKNOWN  # static | dynamic | unknown
    align: int = 1  # 动态输入下的对齐倍数；1 = 无静态约束
    reason: str = ""

    @property
    def fixed_tile(self) -> int | None:
        """静态输入边长；非 None 时 `tile` 必须正好等于它。"""
        return self.fixed_hw[0] if self.fixed_hw else None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "dtype": self.dtype,
            "dims": list(self.dims),
            "dynamic_hw": self.dynamic_hw,
            "fixed_hw": list(self.fixed_hw) if self.fixed_hw else None,
            "fixed_tile": self.fixed_tile,
            "channels": self.channels,
            "align": self.align,
            "source": self.source,
            "reason": self.reason,
            "metadata": dict(self.metadata),
        }


def _unknown(reason: str) -> InputSpec:
    return InputSpec(
        name="", dtype="float32", dims=(), dynamic_hw=False, fixed_hw=None,
        channels=None, source=_UNKNOWN, align=1, reason=reason,
    )


def spec_from_session(session: Any) -> InputSpec:
    """从**已建好的** ORT session 读输入契约（执行器走这条，零额外代价）。"""
    try:
        inputs = session.get_inputs()
    except Exception as exc:  # 非 ORT 对象 / session 已释放
        return _unknown(f"无法读取模型输入定义：{exc}")
    if not inputs:
        return _unknown("模型没有任何输入，无法确定输入契约")

    first = inputs[0]
    raw_dims = list(getattr(first, "shape", None) or [])
    ort_type = str(getattr(first, "type", "") or "")
    dtype = _ORT_TYPE_TO_NUMPY.get(ort_type, "float32")

    return _build(
        name=str(getattr(first, "name", "") or ""),
        raw_dims=raw_dims,
        dtype=dtype,
        metadata=_read_metadata(session),
        extra_types=[str(getattr(i, "type", "")) for i in inputs[1:]],
    )


def _read_metadata(session: Any) -> dict[str, str]:
    try:
        meta = session.get_modelmeta()
        raw = getattr(meta, "custom_metadata_map", None) or {}
        return {str(k): str(v) for k, v in dict(raw).items()}
    except Exception:
        return {}


def _build(
    *, name: str, raw_dims: list[Any], dtype: str, metadata: dict[str, str],
    extra_types: list[str] | None = None,
) -> InputSpec:
    # 归一化维度：ORT 用 str（如 'height'/'width'/'batch'）或 int 表示
    dims: list[Any] = []
    for d in raw_dims:
        if isinstance(d, int) and d > 0:
            dims.append(d)
        else:
            dims.append(str(d))
    dims_t = tuple(dims)

    n_dims = len(dims_t)
    if n_dims != 4:
        return InputSpec(
            name=name, dtype=dtype, dims=dims_t, dynamic_hw=False, fixed_hw=None,
            channels=dims_t[1] if n_dims == 4 and isinstance(dims_t[1], int) else None,
            metadata=metadata, source=_UNKNOWN, align=1,
            reason=f"输入维度为 {n_dims}（期望 4：NCHW），无法据 shape 推断分块约束",
        )

    channels = dims_t[1] if isinstance(dims_t[1], int) else None
    h, w = dims_t[2], dims_t[3]
    h_dyn, w_dyn = not isinstance(h, int), not isinstance(w, int)

    if h_dyn or w_dyn:
        return InputSpec(
            name=name, dtype=dtype, dims=dims_t, dynamic_hw=True, fixed_hw=None,
            channels=channels, metadata=metadata, source=_DYNAMIC, align=1,
            reason=(
                "输入 H/W 为动态维度：ONNX shape 读不出窗口/步长类约束"
                "（SwinIR 类需 8 的倍数，纯卷积无约束，两者 shape 相同）——"
                "此类约束须由模型元信息声明（T-700 冻结核对项）；"
                "此处 align=1 的含义是「未检测到约束」，调用方应保留保守下界而非据此放宽"
            ),
        )

    if h != w:
        return InputSpec(
            name=name, dtype=dtype, dims=dims_t, dynamic_hw=False, fixed_hw=None,
            channels=channels, metadata=metadata, source=_STATIC, align=1,
            reason=f"输入为固定 {h}×{w}（非正方形）：分块边长无法同时满足两边，按无约束处理",
        )

    return InputSpec(
        name=name, dtype=dtype, dims=dims_t, dynamic_hw=False, fixed_hw=(int(h), int(w)),
        channels=channels, metadata=metadata, source=_STATIC, align=int(h),
        reason=f"输入为固定 {h}×{w}：分块边长必须正好等于 {h}",
    )


# ---------------------------------------------------------------------------
# 按路径读取（带进程内缓存）
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_cache: dict[tuple[str, int, int], InputSpec] = {}


def _cache_key(path: Path) -> tuple[str, int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (str(path.resolve()), int(st.st_size), int(st.st_mtime_ns))


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def spec_for_path(path: str | Path, *, providers: list[str] | None = None) -> InputSpec:
    """读某个 `.onnx` 文件的输入契约（进程内缓存；**永不抛异常**）。

    Args:
        path: `.onnx` 文件路径。
        providers: 建临时 session 用的 EP 列表；默认仅 CPU（读 shape 不需要 GPU，
            而且用 CPU 避免为了读元信息去占显存）。
    """
    p = Path(path)
    key = _cache_key(p)
    if key is None:
        return _unknown(f"模型文件不存在或不可读：{p}")
    with _lock:
        hit = _cache.get(key)
    if hit is not None:
        return hit

    spec = _read_with_temp_session(p, providers or ["CPUExecutionProvider"])
    with _lock:
        _cache[key] = spec
    return spec


def _read_with_temp_session(path: Path, providers: list[str]) -> InputSpec:
    try:
        import onnxruntime as ort  # 惰性 import：模块可用性由 runtimes.py 负责
    except Exception as exc:
        return _unknown(f"onnxruntime 不可用，无法读取输入约束：{exc}")
    try:
        so = ort.SessionOptions()
        so.log_severity_level = 3
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL  # 只为读元信息
        sess = ort.InferenceSession(str(path), sess_options=so, providers=providers)
    except Exception as exc:
        return _unknown(f"打开模型读取输入约束失败：{type(exc).__name__}: {exc}")
    try:
        return spec_from_session(sess)
    finally:
        del sess
