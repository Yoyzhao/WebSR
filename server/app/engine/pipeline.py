"""M2 推理编排：预处理 → 分块 → 逐块推理 → 羽化拼接 → 后处理 → 落盘（T-806）。

本模块把 T-802（M4 纯算法）、T-804（阶段 D 决策）、T-806（模型后端）串成**一条真实
可跑的推理链路**，是 S1"上传 → 任务 → 推理 → 对比 → 下载"闭环里最后缺失的一环。

## 它为什么在引擎层，以及为什么纯

`engine/` 不 import fastapi / sqlalchemy / pydantic，因此本模块**可以在任意 Python 环境
独立复核**——这正是 `.venvs/sr-ov` / `.venvs/sr-ncnn` 里能真跑一遍 IR / ncnn 后端的原因
（那两个环境没有 FastAPI 栈，却有对应运行时）。"产物入库、任务状态"这些事由应用层
（`tasks/executor.py` + `tasks/manager.py`）负责。

## 与 M4 的所有权边界（tech-arch §2.2 不变量 3 / 4）

- M4（`image_ops` / `tiling`）是**无状态纯算法**，不编排循环、不感知后端与进度；
- 循环、取消自检、进度上报、每块结果的生命周期，**全部在本模块**；
- `tile` / `overlap` / `feather_px` 一律来自阶段 D 的决策结果，本模块**不决定数值**。

## 取消的语义

取消是**协作式**的（ADR-005 约束 3）：本模块只在块与块之间检查标志，抛 `InferCancelled`；
应用层把它映射成 `TaskCancelled`。不强杀线程——强杀会让 ORT / OpenVINO 的会话处于
未定义状态。
"""
from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import image_ops, tiling
from .image_ops import ImageMeta, PreprocessSpec
from .model_loader import InferenceBackend

logger = logging.getLogger("websr.engine.pipeline")

__all__ = ["InferCancelled", "UpscaleRequest", "UpscaleOutcome", "run_upscale"]

_HASH_CHUNK = 1024 * 1024


class InferCancelled(Exception):
    """协作式取消（引擎层），由应用层映射为 `TaskCancelled`。"""


@dataclass(frozen=True)
class UpscaleRequest:
    """一次超分任务的编排输入。**数值全部来自调用方**（阶段 D 决策 + 模型约束）。"""

    source_path: Path
    output_path: Path
    tile: int
    overlap: int
    feather_px: int
    scale: int
    spec: PreprocessSpec
    should_cancel: Callable[[], bool] | None = None
    #: `(chunk_done, chunk_total, stage, message)`；节流与落库由调用方负责
    report: Callable[[int, int, str, str], None] | None = None


@dataclass(frozen=True)
class UpscaleOutcome:
    """执行结果（**真实产物**，不是决策）。"""

    output_path: Path
    width: int
    height: int
    source_width: int
    source_height: int
    tiles: int
    scale: int
    elapsed_ms: int
    size_bytes: int
    sha256: str
    backend: dict

    def to_dict(self) -> dict:
        return {
            "output_path": str(self.output_path),
            "width": self.width,
            "height": self.height,
            "source_width": self.source_width,
            "source_height": self.source_height,
            "tiles": self.tiles,
            "scale": self.scale,
            "elapsed_ms": self.elapsed_ms,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "backend": dict(self.backend),
        }


def _emit(req: UpscaleRequest, done: int, total: int, stage: str, message: str) -> None:
    if req.report is not None:
        req.report(done, total, stage, message)


def _check_cancel(req: UpscaleRequest) -> None:
    if req.should_cancel is not None and req.should_cancel():
        raise InferCancelled()


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            buf = fh.read(_HASH_CHUNK)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def run_upscale(backend: InferenceBackend, req: UpscaleRequest) -> UpscaleOutcome:
    """跑完一次超分：返回真实产物信息。取消抛 `InferCancelled`，失败抛异常。"""
    if req.tile < 1:
        raise ValueError(f"tile 必须 >= 1，得到 {req.tile}")
    if not (0 <= req.overlap < req.tile):
        raise ValueError(f"overlap 必须满足 0 <= overlap < tile，得到 {req.overlap} / {req.tile}")
    if req.scale < 1:
        raise ValueError(f"scale 必须 >= 1，得到 {req.scale}")

    t0 = time.perf_counter()

    # ---- 1. 预处理 -----------------------------------------------------------
    _check_cancel(req)
    _emit(req, 1, 1, "preprocessing", "正在预处理输入图像")
    tensor, meta = image_ops.to_tensor(req.source_path, req.spec)
    src_h, src_w = int(tensor.shape[2]), int(tensor.shape[3])
    channels = int(tensor.shape[1])
    if (src_w, src_h) != meta.size:
        raise RuntimeError(f"预处理尺寸自相矛盾：张量 {src_w}x{src_h} vs 元信息 {meta.size}")

    # ---- 2. 分块计划 ---------------------------------------------------------
    plan = tiling.plan_tiles(src_h, src_w, req.tile, req.overlap)
    total = len(plan.boxes)
    logger.info(
        "分块计划：%dx%d → tile=%d overlap=%d stride=%d，共 %d 块（%d 行 x %d 列）",
        src_w, src_h, plan.tile, plan.overlap, plan.stride, total, plan.rows, plan.cols,
    )

    # ---- 3. 逐块推理 + 羽化累加 ----------------------------------------------
    acc = tiling.TileAccumulator(plan, scale=req.scale, feather_px=req.feather_px, channels=channels)
    for i, box in enumerate(plan.boxes, 1):
        _check_cancel(req)
        tile_in = tiling.extract_tile(tensor, box, plan.tile)
        tile_out = backend.infer(tile_in)
        acc.add(box, tile_out)
        _emit(req, i, total, "inferencing", f"正在推理 {i} / {total} 块")

    _check_cancel(req)
    _emit(req, total, total, "stitching", "正在拼接分块结果")
    stitched = acc.finalize()

    # ---- 4. 后处理 + 落盘 ----------------------------------------------------
    _emit(req, total, total, "saving", "正在保存结果")
    img = image_ops.from_tensor(stitched, scale=req.scale, meta=meta)
    out_path = Path(req.output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    image_ops.save_image(img, out_path)

    # ---- 5. 产物事实 ---------------------------------------------------------
    actual_w, actual_h = img.size
    expected = plan.out_size(req.scale)
    if (actual_w, actual_h) != expected:
        # from_tensor 会按 原图×scale 对齐；这里断言"对齐后确实等于计划尺寸"
        raise RuntimeError(f"产物尺寸异常：{actual_w}x{actual_h}，期望 {expected[0]}x{expected[1]}")

    sha = _sha256_of(out_path)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return UpscaleOutcome(
        output_path=out_path,
        width=actual_w,
        height=actual_h,
        source_width=meta.size[0],
        source_height=meta.size[1],
        tiles=total,
        scale=req.scale,
        elapsed_ms=elapsed_ms,
        size_bytes=out_path.stat().st_size,
        sha256=sha,
        backend=dict(backend.describe()),
    )
