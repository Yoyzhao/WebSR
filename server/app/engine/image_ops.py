"""M4 图像处理 —— 预处理与后处理（**无状态纯算法库**）。

架构约束（tech-arch §2.2 不变量 3 / 4）：

- **无状态**：不持有会话、不缓存、不编排推理循环、不感知 EP / 档位 / 进度；
- **不选后端**：通道顺序、归一化系数、精度等一律由调用方（M2）作为**参数**传入——本模块
  不猜、不按设备或模型名分支（project-rules 铁律：禁止按型号硬编码）；
- **不依赖推理栈**：只依赖 numpy + Pillow，可脱离 onnxruntime / openvino 独立运行与测试。

本模块只回答两个问题：

1. 磁盘上的图片 → 模型输入张量（`to_tensor`）；
2. 模型输出张量 → 可落盘的图片（`from_tensor`）。

> 归一化契约（`PreprocessSpec`）应由**模型元信息**声明。当前内置 ONNX 模型统一按
> RGB / `/255` / float32 处理（与 RealESRGAN 官方 ONNX 导出一致）；模型级声明字段属
> T-700 契约冻结核对项，见 `docs/plan/tasks/M5/STEP-5D.md` §T-802。
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps

__all__ = [
    "PreprocessSpec",
    "ImageMeta",
    "open_image",
    "to_tensor",
    "from_tensor",
    "encode_image",
    "save_image",
]

# 可接受的输入源：路径 / 原始字节 / 文件对象（FastAPI 的 UploadFile.file 亦可）
Source = str | Path | bytes | bytearray | io.BytesIO

#: 后处理反归一化时的数值下限，避免除零（工程防御，不是"参数"）
_EPS = 1e-8


@dataclass(frozen=True)
class PreprocessSpec:
    """模型输入契约。**由 M2 依据模型元信息构造后传入**，M4 只负责执行。

    Args:
        channel_order: `"rgb"`（RealESRGAN 等多数 ONNX 导出）或 `"bgr"`。
        scale: 像素乘数，把 uint8 的 [0, 255] 映射到模型期望值域（默认 `/255`）。
        mean / std: 可选的逐通道标准化 `(x/255 - mean) / std`；`None` 表示不做。
        dtype: 输出张量类型（`"float32"` / `"float16"`）。
    """

    channel_order: str = "rgb"
    scale: float = 1.0 / 255.0
    mean: tuple[float, float, float] | None = None
    std: tuple[float, float, float] | None = None
    dtype: str = "float32"

    def __post_init__(self) -> None:
        if self.channel_order not in ("rgb", "bgr"):
            raise ValueError(f"channel_order 必须是 'rgb' 或 'bgr'，得到 {self.channel_order!r}")
        if self.dtype not in ("float32", "float16"):
            raise ValueError(f"dtype 仅支持 float32 / float16，得到 {self.dtype!r}")
        if (self.mean is None) != (self.std is None):
            raise ValueError("mean 与 std 必须同时给出或同时为 None")


@dataclass(frozen=True)
class ImageMeta:
    """预处理时留存的、后处理需要的信息（随张量一起传递）。"""

    size: tuple[int, int]  # 原图尺寸 (w, h)，**已应用 EXIF 方向修正**
    mode: str  # 原图色彩模式（EXIF 修正前）
    format: str | None  # 原图格式（如 "JPEG"）
    alpha: np.ndarray | None  # (h, w) uint8 透明度通道；无 alpha 时为 None

    @property
    def has_alpha(self) -> bool:
        return self.alpha is not None


def open_image(source: Source) -> Image.Image:
    """打开并**完整解码**图片，同时按 EXIF 方向转正。

    - `load()` 强制真实解码：截断/损坏的文件在此暴露（上传二次校验同一判据）；
    - `exif_transpose` 修正手机竖拍的 Orientation，**避免"预览是横的、结果也是横的"**
      ——原图方向错了，超分只会忠实放大错误朝向。

    `bytes` / `bytearray` 会被包成内存流（`PIL.Image.open` 只接受路径或文件对象，
    直接传 bytes 会被当成文件名，在中文 Windows 上以 UnicodeDecodeError 收场）。
    """
    if isinstance(source, (bytes, bytearray, memoryview)):
        source = io.BytesIO(bytes(source))
    img = Image.open(source)
    img.load()
    fmt = img.format  # exif_transpose 会返回新对象并丢掉 format，先留存
    out = ImageOps.exif_transpose(img)
    if out.format != fmt:
        out.format = fmt
    return out


def to_tensor(
    source: Source,
    spec: PreprocessSpec | None = None,
) -> tuple[np.ndarray, ImageMeta]:
    """图片 → 模型输入张量。

    Returns:
        `(tensor, meta)`：`tensor` 形如 `(1, 3, H, W)`；`meta` 供 `from_tensor` 使用。
    """
    spec = spec or PreprocessSpec()
    img = open_image(source)
    fmt = img.format
    src_mode = img.mode

    # alpha 单独留存：ONNX 超分模型只吃 3 通道，输出时再以最近邻放大回填
    alpha: np.ndarray | None = None
    if "A" in img.getbands():
        alpha = np.asarray(img.getchannel("A"), dtype=np.uint8)

    rgb = img.convert("RGB")
    arr = np.asarray(rgb, dtype=np.float32)  # (H, W, 3)，uint8 → float32 无损

    if spec.channel_order == "bgr":
        arr = arr[..., ::-1]

    arr = np.ascontiguousarray(arr) * np.float32(spec.scale)

    if spec.mean is not None and spec.std is not None:
        mean = np.asarray(spec.mean, dtype=np.float32).reshape(1, 1, 3)
        std = np.asarray(spec.std, dtype=np.float32).reshape(1, 1, 3)
        arr = (arr - mean) / np.maximum(std, _EPS)

    chw = np.ascontiguousarray(arr.transpose(2, 0, 1)[None])  # (1, 3, H, W)
    if spec.dtype == "float16":
        chw = chw.astype(np.float16)

    meta = ImageMeta(size=(img.width, img.height), mode=src_mode, format=fmt, alpha=alpha)
    return chw, meta


def from_tensor(
    tensor: Any,
    *,
    scale: int,
    meta: ImageMeta,
    clamp: bool = True,
) -> Image.Image:
    """模型输出张量 → 可落盘图片。

    Args:
        tensor: `(1, 3, H*scale, W*scale)` 或 `(3, H, W)`，值域 `[0, 1]`。
        scale: 放大倍数（用于确定目标尺寸 `src * scale`）。
        meta: `to_tensor` 返回的元信息。
        clamp: 是否把值域钳制到 `[0, 1]`（模型可能溢出）。

    Returns:
        RGB 或 RGBA（原图带 alpha 时）的 `PIL.Image`。
    """
    if scale < 1:
        raise ValueError(f"scale 必须 >= 1，得到 {scale}")

    arr = np.asarray(tensor)
    if arr.ndim == 4:
        if arr.shape[0] != 1:
            raise ValueError(f"仅支持 batch=1，得到 shape={arr.shape}")
        arr = arr[0]
    if arr.ndim != 3 or arr.shape[0] != 3:
        raise ValueError(f"期望 (3, H, W) 的 CHW 张量，得到 shape={arr.shape}")

    arr = arr.astype(np.float32)
    if clamp:
        arr = np.clip(arr, 0.0, 1.0)

    hwc = np.ascontiguousarray(arr.transpose(1, 2, 0))
    u8 = np.rint(hwc * 255.0).astype(np.uint8)
    img = Image.fromarray(u8, mode="RGB")

    # 目标尺寸以**原图 × 倍数**为准：模型输出若因 pad / 非整除而偏大偏小，在此对齐
    tw, th = meta.size[0] * scale, meta.size[1] * scale
    if img.size != (tw, th):
        img = _fit_size(img, (tw, th))

    if meta.alpha is not None:
        a = Image.fromarray(meta.alpha, mode="L").resize((tw, th), Image.Resampling.NEAREST)
        out = img.convert("RGBA")
        out.putalpha(a)
        return out
    return img


def _fit_size(img: Image.Image, target: tuple[int, int]) -> Image.Image:
    """把图对齐到目标尺寸：偏大则居中裁剪，偏小则最近邻放大。

    居中裁剪而非左上裁剪，是为了让"模型输出比预期略大"时裁掉的是四周多余边而非右侧。
    """
    tw, th = target
    w, h = img.size
    if w >= tw and h >= th:
        left, top = (w - tw) // 2, (h - th) // 2
        return img.crop((left, top, left + tw, top + th))
    return img.resize((tw, th), Image.Resampling.NEAREST)


def encode_image(img: Image.Image, format: str = "PNG", **kwargs: Any) -> bytes:
    """图片 → 字节（格式转换出口）。

    PNG 无损（默认，质量红线：结果图默认不做有损二次编码）；
    JPEG 走 `quality=95, subsampling=0`（4:4:4，避免色度抽样放大后可见）。
    """
    fmt = format.upper()
    buf = io.BytesIO()
    if fmt in ("JPG", "JPEG"):
        kwargs.setdefault("quality", 95)
        kwargs.setdefault("subsampling", 0)
        if img.mode == "RGBA":
            img = img.convert("RGB")
    elif fmt == "PNG":
        kwargs.setdefault("compress_level", 6)
    img.save(buf, format=fmt, **kwargs)
    return buf.getvalue()


def save_image(img: Image.Image, path: str | Path, **kwargs: Any) -> Path:
    """按扩展名落盘（`encode_image` 的便捷封装）。"""
    p = Path(path)
    data = encode_image(img, format=p.suffix.lstrip(".") or "PNG", **kwargs)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p
