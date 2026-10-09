"""M4 图像处理 —— 分块计划、羽化权重与拼接累加（**无状态纯算法库**）。

## 为什么必须重写（质量红线）

tech-arch §6.5 / PRD F-11：**"仅平移窗口"或"仅加 pad"都会留下可见接缝**。
`tools/_bench_common.py::tiled_infer` 是基准用的**形状验证器**（无 overlap、无羽化、
边界块按窗口平移处理），**生产实现必须重写**——本模块即重写产物。

接缝的根因：模型对每个块的输出都带有**边界效应**（卷积核在块边缘缺少上下文），
块内容又因归一化/激活而**依赖块内统计量**，两块各自推理后直接拼贴，边缘处必然出现
阶跃。两种朴素做法都不解决问题：

| 做法 | 结果 |
|---|---|
| 硬切（无重叠） | 相邻块边界上出现**阶跃线** |
| 平移窗口（`tiled_infer` 的做法） | 仍是硬切，只是每块都取到完整尺寸；接缝照旧 |
| 重叠但直接覆盖写 | 后写的块覆盖先写的块，接缝从块边界移到**覆盖边界** |

正确做法是 **overlap + feather 加权混合**：每块按其位置生成一个边缘渐隐的权重窗，
逐像素加权累加，最后**除以权重和**（加权平均）。因为做了归一化，只要每个像素至少被一块
以正权重覆盖，结果就是凸组合——既平滑又不改变整体亮度。

## 本模块的边界（tech-arch §2.2 不变量 3 / 4）

- **不编排循环**：`plan_tiles()` 只给出块清单，逐块推理由 M2 驱动（它能边推理边 `add()`
  以及时释放显存、上报进度）；
- **不做后端选择**：`tile` / `overlap` / `feather_px` 一律作为**参数**由 M2 的决策结果传入；
- **不感知 EP 与进度**：本模块看不到任何后端概念，可脱离推理栈独立测试。

## 分块排布约定

所有块都是**完整 `tile × tile`**（静态 shape 模型的前提），且**完整落在原图内**：
位置从 0 起按 `stride = tile - overlap` 递增，末尾补一个贴边的收尾块。

```
y:  0 ──────── tile
        ├── overlap ──┤
    stride=448            倒数第二块
                          └── 收尾块（贴图底，与前块重叠 ≥ overlap）
```

由于收尾块贴边，**内部重叠量恒为 `overlap`，收尾处重叠量 ≥ `overlap`** —— 变大的重叠
只会让权重和更大，加权平均依旧成立。这样避免了"pad 到网格"带来的一整圈虚假边缘。

唯一的例外是**原图短边小于 `tile`**（此时只有一个块，必须补齐到固定尺寸）：块内容
按**居中**放在 tile 中央并用**边缘复制（edge）**填充，让内容离块边界尽可能远。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import numpy as np

__all__ = [
    "TileBox",
    "TilePlan",
    "plan_tiles",
    "feather_window",
    "box_weight",
    "extract_tile",
    "TileAccumulator",
    "stitch",
]

#: 归一化保护下界。羽化窗在渐隐区末端权重为 0，理论上"某像素的全部覆盖块在此处
#: 权重都为 0"不会发生（渐隐只发生在 `has_*` 为真的侧，而该侧必有邻居块以权重 ≈1
#: 覆盖），此下界只是把这种不可能情形兜住，避免除零。属工程防御，不是可调参数。
_W_MIN = 1e-6


@dataclass(frozen=True)
class TileBox:
    """一个分块的位置与邻居关系（全部为**原图坐标**）。

    `content_h` / `content_w` 是该块覆盖的原图像素尺寸；只有"原图短边 < tile"时才会
    小于 `tile`，此时内容居中放置，`pad_top` / `pad_left` 为内容在 tile 内的偏移。

    `has_*` 表示该侧**是否存在相邻块**——决定权重窗在该侧是否渐隐：
    贴图边的一侧没有邻居，渐隐只会白白压低权重（归一化后仍需除回来），故保持为 1。
    """

    y0: int
    x0: int
    content_h: int
    content_w: int
    pad_top: int
    pad_left: int
    has_top: bool
    has_bottom: bool
    has_left: bool
    has_right: bool

    @property
    def y1(self) -> int:
        return self.y0 + self.content_h

    @property
    def x1(self) -> int:
        return self.x0 + self.content_w


@dataclass(frozen=True)
class TilePlan:
    """一次分块的完整计划（不可变；可安全地在多线程间共享）。"""

    tile: int
    overlap: int
    stride: int
    src_h: int
    src_w: int
    rows: int
    cols: int
    boxes: tuple[TileBox, ...]

    def __len__(self) -> int:
        return len(self.boxes)

    @property
    def needs_pad(self) -> bool:
        """原图是否小到必须补齐（此时拼接结果仍以原图为准，pad 区域不写回）。"""
        return self.src_h < self.tile or self.src_w < self.tile

    def out_size(self, scale: int) -> tuple[int, int]:
        """输出尺寸 `(w, h)`——恒为 `原图 × scale`，**不含 pad 残留**。"""
        return self.src_w * scale, self.src_h * scale


def _positions(src: int, tile: int, stride: int) -> list[int]:
    """一维块起点：步进 + 贴边收尾，全部落在 `[0, src - tile]`。"""
    if src <= tile:
        return [0]
    pos = list(range(0, src - tile + 1, stride))
    if pos[-1] != src - tile:
        pos.append(src - tile)  # 收尾块：贴住尾边，与前块重叠量 >= overlap
    return pos


def plan_tiles(src_h: int, src_w: int, tile: int, overlap: int) -> TilePlan:
    """生成分块计划。

    Args:
        src_h / src_w: 原图高宽（像素）。
        tile: 块边长（由阶段 C 标定决定，**不写死**）。
        overlap: 相邻块重叠像素数，必须 `0 <= overlap < tile`。
            `overlap = 0` 等价于硬切——**仅用于对照实验**，产品路径禁用。

    Raises:
        ValueError: 参数非法。
    """
    if src_h < 1 or src_w < 1:
        raise ValueError(f"原图尺寸必须为正，得到 {src_h}x{src_w}")
    if tile < 1:
        raise ValueError(f"tile 必须 >= 1，得到 {tile}")
    if overlap < 0:
        raise ValueError(f"overlap 不能为负，得到 {overlap}")
    if overlap >= tile:
        raise ValueError(f"overlap 必须小于 tile（{overlap} >= {tile}），否则 stride 退化为 0")

    stride = tile - overlap
    ys = _positions(src_h, tile, stride)
    xs = _positions(src_w, tile, stride)

    content_h, content_w = min(tile, src_h), min(tile, src_w)
    pad_top = (tile - content_h) // 2  # 仅"图小于 tile"时非零：内容居中
    pad_left = (tile - content_w) // 2

    boxes: list[TileBox] = []
    for y0 in ys:
        for x0 in xs:
            boxes.append(
                TileBox(
                    y0=y0,
                    x0=x0,
                    content_h=content_h,
                    content_w=content_w,
                    pad_top=pad_top,
                    pad_left=pad_left,
                    has_top=y0 > 0,
                    has_bottom=y0 + content_h < src_h,
                    has_left=x0 > 0,
                    has_right=x0 + content_w < src_w,
                )
            )

    return TilePlan(
        tile=tile,
        overlap=overlap,
        stride=stride,
        src_h=src_h,
        src_w=src_w,
        rows=len(ys),
        cols=len(xs),
        boxes=tuple(boxes),
    )


def _ramp_1d(n: int, fade: int, fade_lo: bool, fade_hi: bool) -> np.ndarray:
    """一维羽化窗（长度 `n` = **输出分辨率**下的边长）。

    两个设计要点，都直接关系到"无可见接缝"：

    1. **端点取整**：渐隐区在 `[0, fade-1]` 上从 0 线性升到 1（分母 `fade-1`），
       于是第 `fade` 个像素恰好为 1，与窗外恒为 1 的部分**数值连续**。
       若改用 `(i + 0.5) / fade`，窗内首值是 `1/fade` 而非 0 或 1，会在窗边界留下
       一个小台阶——实测中它正是最大的残留"接缝"来源。
    2. **在输出分辨率上生成**：权重若先在 tile 分辨率生成再用 `repeat` 放大，会变成
       阶梯函数，混合结果仍带台阶。这里直接按输出像素数生成，权重才真正连续。

    渐隐区以外的权重恒为 1；`fade <= 0` 时整窗为 1（**无羽化**，仅用于对照实验）。
    """
    w = np.ones(n, dtype=np.float32)
    if fade <= 0 or not (fade_lo or fade_hi):
        return w
    denom = float(max(fade - 1, 1))
    idx = np.arange(n, dtype=np.float32)
    if fade_lo:
        w = np.minimum(w, np.clip(idx / denom, 0.0, 1.0))
    if fade_hi:
        w = np.minimum(w, np.clip((n - 1 - idx) / denom, 0.0, 1.0))
    return w


def box_weight(tile: int, feather_px: int, box: TileBox, scale: int = 1) -> np.ndarray:
    """按块的邻居关系生成 `(tile*scale, tile*scale)` 权重窗（仅在有邻居的侧渐隐）。

    scale 与结果张量同分辨率；`has_*` 为假的一侧不渐隐（贴图边没有邻居可融合）。
    """
    n = tile * scale
    fade = feather_px * scale
    wh = _ramp_1d(n, fade, box.has_top, box.has_bottom)
    ww = _ramp_1d(n, fade, box.has_left, box.has_right)
    return np.outer(wh, ww).astype(np.float32)


def feather_window(tile: int, feather_px: int, scale: int = 1) -> np.ndarray:
    """四侧都渐隐的参考权重窗（用于单测/可视化；实际拼接用 `box_weight`）。

    `feather_px = 0` 时退化为全 1（**无羽化**），用于对照实验复现接缝。
    """
    if tile < 1:
        raise ValueError(f"tile 必须 >= 1，得到 {tile}")
    return box_weight(
        tile,
        feather_px,
        TileBox(
            y0=1, x0=1, content_h=tile, content_w=tile, pad_top=0, pad_left=0,
            has_top=True, has_bottom=True, has_left=True, has_right=True,
        ),
        scale=scale,
    )


def extract_tile(src: np.ndarray, box: TileBox, tile: int) -> np.ndarray:
    """从原图张量取出该块的模型输入：恒为 `(1, C, tile, tile)`。

    超出图的部分（只有"图小于 tile"时存在）用 **edge 复制**填充——镜像填充会在
    pad 区造出假的纹理边缘，而边缘复制对超分结果最中性。
    """
    if src.ndim != 4:
        raise ValueError(f"期望 (1, C, H, W) 张量，得到 shape={src.shape}")
    crop = src[:, :, box.y0 : box.y1, box.x0 : box.x1]
    if box.content_h == tile and box.content_w == tile:
        return np.ascontiguousarray(crop)
    return np.pad(
        crop,
        (
            (0, 0),
            (0, 0),
            (box.pad_top, tile - box.pad_top - box.content_h),
            (box.pad_left, tile - box.pad_left - box.content_w),
        ),
        mode="edge",
    )


class TileAccumulator:
    """加权累加器：逐块 `add()`，最后 `finalize()`。

    **逐块调用由调用方（M2）驱动**——这样做是为了让推理循环保持单一所有权，
    且每块结果在 `add()` 后即可释放（大 tile 下显存/内存占用与块数无关）。
    """

    def __init__(
        self,
        plan: TilePlan,
        *,
        scale: int,
        feather_px: int,
        channels: int = 3,
    ) -> None:
        if scale < 1:
            raise ValueError(f"scale 必须 >= 1，得到 {scale}")
        if channels < 1:
            raise ValueError(f"channels 必须 >= 1，得到 {channels}")
        self.plan = plan
        self.scale = scale
        self.feather_px = feather_px
        self.channels = channels
        self.out_h = plan.src_h * scale
        self.out_w = plan.src_w * scale

        self._acc = np.zeros((channels, self.out_h, self.out_w), dtype=np.float32)
        self._wacc = np.zeros((1, self.out_h, self.out_w), dtype=np.float32)
        # 权重窗按"邻居标志组合"缓存：最多 2^4 = 16 种，避免每块重算 tile² 浮点
        self._win_cache: dict[tuple[bool, bool, bool, bool], np.ndarray] = {}
        self._added = 0

    # -- 内部 ---------------------------------------------------------------

    def _window(self, box: TileBox) -> np.ndarray:
        key = (box.has_top, box.has_bottom, box.has_left, box.has_right)
        win = self._win_cache.get(key)
        if win is None:
            # 权重窗按输出分辨率生成（**不是**先在 tile 分辨率生成再 repeat 放大——
            # 那会变成阶梯函数，在重叠区留下台阶状残留）。
            win = box_weight(self.plan.tile, self.feather_px, box, scale=self.scale)
            self._win_cache[key] = win
        return win

    # -- 对外 ---------------------------------------------------------------

    @property
    def added(self) -> int:
        """已累加的块数（M2 可据此上报 chunk 级进度）。"""
        return self._added

    def add(self, box: TileBox, result: Any) -> None:
        """累加一块推理结果。

        Args:
            box: `plan_tiles` 给出的块。
            result: `(1, C, tile*scale, tile*scale)` 的块输出（值域 `[0, 1]`）。
        """
        s = self.scale
        tile_out = self.plan.tile * s
        arr = np.asarray(result)
        if arr.ndim != 4 or arr.shape[0] != 1:
            raise ValueError(f"块结果期望 (1, C, H, W)，得到 shape={arr.shape}")
        if arr.shape[1] != self.channels:
            raise ValueError(f"通道数不匹配：期望 {self.channels}，得到 {arr.shape[1]}")
        if arr.shape[2] != tile_out or arr.shape[3] != tile_out:
            raise ValueError(
                f"块结果尺寸不匹配：期望 {tile_out}x{tile_out}（tile={self.plan.tile} × scale={s}），"
                f"得到 {arr.shape[2]}x{arr.shape[3]}"
            )

        # 有效内容在块结果中的窗口（裁掉"图小于 tile"时补齐的 pad 区）
        top, left = box.pad_top * s, box.pad_left * s
        ch, cw = box.content_h * s, box.content_w * s

        w = self._window(box)[None, top : top + ch, left : left + cw]
        r = arr[0][:, top : top + ch, left : left + cw].astype(np.float32, copy=False)

        oy, ox = box.y0 * s, box.x0 * s
        self._acc[:, oy : oy + ch, ox : ox + cw] += r * w
        self._wacc[:, oy : oy + ch, ox : ox + cw] += w
        self._added += 1

    def finalize(self, *, clamp: bool = True) -> np.ndarray:
        """归一化并返回 `(C, H*scale, W*scale)` 结果。

        逐像素除以权重和——这一步是"羽化"真正生效的地方：重叠区内两块权重互补，
        归一化后自然融合，不会出现亮度塌陷或叠加增亮。
        """
        out = self._acc / np.maximum(self._wacc, _W_MIN)
        return np.clip(out, 0.0, 1.0) if clamp else out


def stitch(
    plan: TilePlan,
    results: Sequence[Any] | Iterable[Any],
    *,
    scale: int,
    feather_px: int,
    channels: int = 3,
    clamp: bool = True,
) -> np.ndarray:
    """纯函数式拼接：结果已全部就绪时使用（`TileAccumulator` 的便捷封装）。

    结果须与 `plan.boxes` **顺序一致**。流式场景（边推理边释放显存）请直接用
    `TileAccumulator`。
    """
    acc = TileAccumulator(plan, scale=scale, feather_px=feather_px, channels=channels)
    n = 0
    for box, res in zip(plan.boxes, results):
        acc.add(box, res)
        n += 1
    if n != len(plan.boxes):
        raise ValueError(f"结果数量与块数不一致：期望 {len(plan.boxes)}，得到 {n}")
    return acc.finalize(clamp=clamp)
