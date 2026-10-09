"""T-802 M4 图像处理验证：预处理 / 分块 / overlap + feather 拼接。

纯算法库验证——**不启动 FastAPI、不加载推理栈**，这也顺带证明 M4 与后端解耦
（tech-arch §2.2 不变量 4）。

核心方法论：**"无接缝"这个结论必须可证伪**。
所以本脚本不是直接断言"结果很平滑"，而是先构造一个**已知会产生接缝的对照组**
（无重叠硬切），确认接缝指标能把它测出来（判别力），再在同一指标下比较本实现。
若对照组都测不出接缝，则"本实现无接缝"的断言毫无意义。

运行：./.venvs/sr-app/Scripts/python.exe scripts/test-script/verify_t802_image.py
"""
import io
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))

PASS = FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


from PIL import Image  # noqa: E402

from app.engine import tiling  # noqa: E402
from app.engine.image_ops import (  # noqa: E402
    ImageMeta,
    PreprocessSpec,
    encode_image,
    from_tensor,
    open_image,
    to_tensor,
)
from app.engine.tiling import (  # noqa: E402
    TileAccumulator,
    TileBox,
    extract_tile,
    feather_window,
    plan_tiles,
    stitch,
)

# ---------------------------------------------------------------------------
# 假推理器（本任务不引入推理栈，全部用可解析的替代实现）
# ---------------------------------------------------------------------------
SCALE = 2


def infer_pixelwise(x: np.ndarray) -> np.ndarray:
    """逐像素推理：2× 最近邻上采样（等价于"放大"）。

    逐像素意味着块之间**没有耦合**，因此分块拼接的结果应当与整图推理
    **逐像素精确相等**——这是校验坐标映射 / pad 裁剪 / 权重归一化的最强断言。
    """
    return np.repeat(np.repeat(x, SCALE, axis=2), SCALE, axis=3)


def make_local_contrast(scale: int = 1):
    """位置敏感推理器工厂：块内对比度归一化到 [0, 1]（可选先按 scale 最近邻放大）。

    同一内容在不同块位置会得到不同输出，因此**块边界上必然不连续**——
    这正是用来给"接缝指标"提供判别力的接缝源。
    """

    def _infer(x: np.ndarray) -> np.ndarray:
        y = np.repeat(np.repeat(x, scale, axis=2), scale, axis=3) if scale > 1 else x
        lo = y.min(axis=(2, 3), keepdims=True)
        hi = y.max(axis=(2, 3), keepdims=True)
        return (y - lo) / np.maximum(hi - lo, 1e-6)

    return _infer


def run_tiled(infer, src: np.ndarray, plan, *, feather_px: int, scale: int = SCALE) -> np.ndarray:
    """模拟 M2 的编排：逐块取块 → 推理 → 累加（M4 只提供无状态部件）。"""
    acc = TileAccumulator(plan, scale=scale, feather_px=feather_px, channels=src.shape[1])
    for box in plan.boxes:
        acc.add(box, infer(extract_tile(src, box, plan.tile)))
    return acc.finalize()


def run_hard_cut(infer, src: np.ndarray, tile: int, *, scale: int = SCALE) -> np.ndarray:
    """对照组：**无重叠硬切**（prototype 式拼贴）——用于证明接缝指标有判别力。"""
    _, c, h, w = src.shape
    out = np.zeros((c, h * scale, w * scale), dtype=np.float32)
    for y0 in range(0, h, tile):
        for x0 in range(0, w, tile):
            y1, x1 = min(y0 + tile, h), min(x0 + tile, w)
            crop = src[:, :, y0:y1, x0:x1]
            ph, pw = y1 - y0, x1 - x0
            if ph < tile or pw < tile:
                padded = np.zeros((1, c, tile, tile), dtype=src.dtype)
                padded[:, :, :ph, :pw] = crop
                crop = padded
            res = infer(crop)[0][:, : ph * scale, : pw * scale]
            out[:, y0 * scale : y1 * scale, x0 * scale : x1 * scale] = res
    return out


def seam_metrics(rgb: np.ndarray) -> dict:
    """接缝指标（在灰度上算，避免通道差异干扰）。

    - `max_d1`：水平一阶差分最大值（阶跃幅度）
    - `max_d2`：水平**二阶**差分最大值（**阶跃的判别器**）

    为什么看二阶：硬切产生的是"台阶"——一阶差分在边界处出现孤立尖峰，二阶差分
    同处出现更尖锐的尖峰；而羽化做的是**线性混合**，线性函数的二阶差分为 0，
    所以干净的羽化结果 `max_d2` 应回到真值水平（≈ 0）。
    """
    g = rgb.mean(axis=0) if rgb.ndim == 3 else rgb
    d1 = np.abs(np.diff(g, axis=1))
    d2 = np.abs(np.diff(g, n=2, axis=1))
    return {"max_d1": float(d1.max()), "max_d2": float(d2.max())}


def boundary_peak_ratio(rgb: np.ndarray, boundaries: list[int], half: int = 8) -> float:
    """**孤立阶跃尖峰**指标：块边界列的跨列差分 / 邻域中位差分。

    这是最贴合人眼观感的判据——接缝的本质不是"梯度大"，而是**该处梯度相对周围
    突然变大**。羽化会在整个重叠区均匀抬升梯度（平滑但略糊），比值仍 ≈ 1；
    硬切只在边界那一列出现尖峰，比值可达数百。
    """
    g = rgb.mean(axis=0) if rgb.ndim == 3 else rgb
    col = np.abs(np.diff(g, axis=1)).max(axis=0)
    ratios = []
    for b in boundaries:
        j = b - 1  # 跨界差分在 d1 的索引
        if j <= 0 or j >= col.size:
            continue
        lo, hi = max(0, j - half), min(col.size, j + half + 1)
        idx = np.arange(lo, hi)
        neigh = col[idx[idx != j]]
        ratios.append(float(col[j]) / max(float(np.median(neigh)), 1e-9))
    return float(np.max(ratios)) if ratios else 0.0


def gradient_src(h: int, w: int) -> np.ndarray:
    """水平线性渐变（1,3,h,w）——真值本身二阶差分为 0，任何尖峰都来自拼接。"""
    ramp = np.linspace(0.0, 1.0, w, dtype=np.float32)[None, :]
    plane = np.broadcast_to(ramp, (h, w)).astype(np.float32)
    return np.stack([plane, plane, plane], axis=0)[None]


# ===========================================================================
print("\n=== 1. 分块计划 plan_tiles ===\n")

plan = plan_tiles(1000, 800, tile=256, overlap=64)
check("stride = tile - overlap", plan.stride == 192, f"got {plan.stride}")
check("块数与网格一致", len(plan) == plan.rows * plan.cols)
check("所有块完整落在图内", all(
    b.y0 >= 0 and b.x0 >= 0 and b.y1 <= 1000 and b.x1 <= 800 for b in plan.boxes))
check("所有块均为完整 tile（图足够大时无 pad）", all(
    b.content_h == 256 and b.content_w == 256 and b.pad_top == 0 and b.pad_left == 0
    for b in plan.boxes), f"needs_pad={plan.needs_pad}")
check("无需补齐", plan.needs_pad is False)

ys = sorted({b.y0 for b in plan.boxes})
xs = sorted({b.x0 for b in plan.boxes})
check("纵向收尾块贴底边（y0 = h - tile）", ys[-1] == 1000 - 256, f"got {ys[-1]}")
check("横向收尾块贴右边（x0 = w - tile）", xs[-1] == 800 - 256, f"got {xs[-1]}")
inner_gaps = [b - a for a, b in zip(ys, ys[1:])]
check("内部步长恒为 stride", all(g == plan.stride for g in inner_gaps[:-1]),
      f"steps={inner_gaps}")
check("收尾处重叠量 >= overlap（不会撕出空洞）", (1000 - 256) - ys[-2] <= plan.stride,
      f"last_step={(1000 - 256) - ys[-2]} stride={plan.stride}")

# 覆盖完整性：每个像素至少被一块覆盖
cover = np.zeros((1000, 800), dtype=np.int32)
for b in plan.boxes:
    cover[b.y0 : b.y1, b.x0 : b.x1] += 1
check("覆盖完整：无任何未覆盖像素", int(cover.min()) >= 1, f"min_coverage={int(cover.min())}")
check("重叠区确实存在（有像素被 >= 2 块覆盖）", int(cover.max()) >= 2,
      f"max_coverage={int(cover.max())}")

for bad, why in [
    (dict(src_h=100, src_w=100, tile=0, overlap=0), "tile=0"),
    (dict(src_h=100, src_w=100, tile=64, overlap=-1), "overlap<0"),
    (dict(src_h=100, src_w=100, tile=64, overlap=64), "overlap>=tile（stride 退化）"),
    (dict(src_h=0, src_w=100, tile=64, overlap=8), "src_h=0"),
]:
    try:
        plan_tiles(**bad)
        check(f"非法参数被拒（{why}）", False, "未抛错")
    except ValueError:
        check(f"非法参数被拒（{why}）", True)

small = plan_tiles(100, 120, tile=256, overlap=64)
check("小图（短边 < tile）：单块且需补齐", len(small) == 1 and small.needs_pad)
check("小图：内容居中补齐（pad = (tile - src) // 2）",
      small.boxes[0].pad_top == (256 - 100) // 2 and small.boxes[0].pad_left == (256 - 120) // 2)
check("小图：输出尺寸仍以原图为准", small.out_size(SCALE) == (120 * SCALE, 100 * SCALE))

# ===========================================================================
print("\n=== 2. 拼接正确性：与整图推理逐像素一致（逐像素推理器） ===\n")

src = gradient_src(1000, 800)
whole = infer_pixelwise(src)[0]  # (3, 2000, 1600)
tiled = run_tiled(infer_pixelwise, src, plan, feather_px=64)
check("输出尺寸 = 原图 × scale", tiled.shape == (3, 2000, 1600), f"got {tiled.shape}")
diff = float(np.abs(tiled - whole).max())
check("分块拼接 == 整图推理（逐像素精确相等）", diff <= 1e-6, f"max|diff|={diff}")

# 权重归一化后不应有亮度漂移：常量图应原样还原
const = np.full((1, 3, 700, 900), 0.42, dtype=np.float32)
ct = run_tiled(infer_pixelwise, const, plan_tiles(700, 900, 256, 64), feather_px=64)
check("常量图无亮度漂移（加权平均归一化正确）",
      float(np.abs(ct - 0.42).max()) <= 1e-6, f"max|diff|={float(np.abs(ct - 0.42).max())}")

# 全 1 覆盖测试：每块输出全 1 → 结果必须全 1（无未覆盖空洞、无权重塌陷）
plan2 = plan_tiles(1000, 800, 256, 64)


class _Ones:
    def __call__(self, x):
        return np.ones((1, x.shape[1], x.shape[2] * SCALE, x.shape[3] * SCALE), dtype=np.float32)


ones = run_tiled(_Ones(), src, plan2, feather_px=64)
check("覆盖完整性：全 1 块 → 结果全 1（无空洞、无权重塌陷）",
      float(np.abs(ones - 1.0).max()) <= 1e-6, f"max|diff|={float(np.abs(ones - 1.0).max())}")

# ===========================================================================
print("\n=== 3. 接缝判别力对照（质量红线） ===\n")

# 用位置敏感推理器：块边界必然不连续，是"接缝源"。
# 这里刻意取 scale=1——推理器输出本身逐像素连续，于是二阶差分的尖峰只能归因于**拼接**，
# 不会被上采样的阶梯污染（放大的正确性已由第 2 节单独覆盖）。
infer_seam = make_local_contrast(1)
TILE, OVL = 64, 16

src_seam = gradient_src(512, 512)
plan_seam = plan_tiles(512, 512, TILE, OVL)
bounds = sorted({b.x1 for b in plan_seam.boxes if b.has_right})

hard = run_hard_cut(infer_seam, src_seam, TILE, scale=1)
feathered = run_tiled(infer_seam, src_seam, plan_seam, feather_px=OVL, scale=1)
truth = infer_seam(src_seam)[0]  # 整图一次推理 = 无接缝基准

m_hard = seam_metrics(hard)
m_feath = seam_metrics(feathered)
m_truth = seam_metrics(truth)
r_hard = boundary_peak_ratio(hard, bounds)
r_feath = boundary_peak_ratio(feathered, bounds)
floor = max(m_truth["max_d2"], 1e-6)

print(f"    硬切     : max_d1={m_hard['max_d1']:.4f}  max_d2={m_hard['max_d2']:.6f}  边界峰比={r_hard:.1f}")
print(f"    羽化     : max_d1={m_feath['max_d1']:.4f}  max_d2={m_feath['max_d2']:.6f}  边界峰比={r_feath:.1f}")
print(f"    整图基准 : max_d1={m_truth['max_d1']:.4f}  max_d2={m_truth['max_d2']:.6f}  边界峰比=-")

check("【判别力】硬切在块边界产生孤立阶跃尖峰（峰比 > 20）", r_hard > 20.0, f"ratio={r_hard:.1f}")
check("【本实现】羽化后块边界不再有孤立尖峰（峰比 < 3）", r_feath < 3.0, f"ratio={r_feath:.1f}")
check("【判别力】硬切的二阶尖峰远高于整图基准（> 100 倍）",
      m_hard["max_d2"] > 100 * floor, f"hard={m_hard['max_d2']:.6f} floor={floor:.6f}")
check("【本实现】羽化把二阶尖峰相对硬切压低 15 倍以上",
      m_feath["max_d2"] * 15 < m_hard["max_d2"],
      f"feather={m_feath['max_d2']:.6f} hard={m_hard['max_d2']:.6f}")
check("【本实现】羽化把最大阶跃相对硬切压低 20 倍以上",
      m_feath["max_d1"] * 20 < m_hard["max_d1"],
      f"feather={m_feath['max_d1']:.4f} hard={m_hard['max_d1']:.4f}")

# 二阶残差的**来源**必须说清楚，否则这条断言会被误读成"还有接缝"：
# 残差来自"相邻两块在重叠区的内容本身差得远"（本实验里 A 端≈1、B 端≈0），
# 羽化把这段差异摊到整个重叠区做平滑过渡，于是在重叠区入口处留下一个**斜率折点**——
# 它是一阶连续（无阶跃）的，表现为边界峰比 ≈ 1。若两块内容一致（逐像素推理器），
# 结果与整图**逐像素精确相等**（见第 2 节），说明羽化本身不引入任何误差。
check("【本实现】二阶残差处于内容曲率量级（< 0.1，且无阶跃——见边界峰比）",
      m_feath["max_d2"] < 0.1, f"got {m_feath['max_d2']:.6f}")

# 权重窗性质
w = feather_window(64, 16)
check("羽化窗：中心为 1", float(w[32, 32]) == 1.0, f"got {float(w[32, 32])}")
check("羽化窗：边缘权重降到 0（该位置必被邻居以权重 ≈1 覆盖）",
      float(w.min()) == 0.0, f"min={float(w.min())}")
check("羽化窗：渐隐区起点与窗外连续（第 fade 个像素恰为 1，不留台阶）",
      float(w[32, 48]) == 1.0 and float(w[32, 47]) == 1.0,
      f"w[32,48]={float(w[32, 48])} w[32,47]={float(w[32, 47])}")
check("羽化窗：feather_px=0 退化为全 1（无羽化对照路径）",
      float(feather_window(64, 0).min()) == 1.0)
check("羽化窗：对称（左右镜像一致）",
      float(np.abs(w - w[:, ::-1]).max()) <= 1e-6)
check("羽化窗：scale>1 时按输出分辨率生成（尺寸同步放大）",
      feather_window(64, 16, scale=4).shape == (256, 256))

# 重叠区权重互补：feather_px == overlap 时，A 的右渐隐窗 + B 的左渐隐窗恒为 1
# → 混合是凸组合，既不会亮度塌陷也不会叠加增亮。
box_a = TileBox(y0=32, x0=0, content_h=64, content_w=64, pad_top=0, pad_left=0,
                has_top=True, has_bottom=True, has_left=False, has_right=True)
box_b = TileBox(y0=32, x0=48, content_h=64, content_w=64, pad_top=0, pad_left=0,
                has_top=True, has_bottom=True, has_left=True, has_right=False)
seg_a = tiling.box_weight(64, 16, box_a)[32, 48:64]  # A 的右渐隐区
seg_b = tiling.box_weight(64, 16, box_b)[32, 0:16]  # B 的左渐隐区（同一片原图区域）
pair_sum = seg_a + seg_b
check("羽化窗：重叠区两块权重互补（和恒为 1 → 凸组合，无亮度塌陷/叠加增亮）",
      float(np.abs(pair_sum - 1.0).max()) <= 1e-6,
      f"max|sum-1|={float(np.abs(pair_sum - 1.0).max())}")

# 贴图边的一侧不应渐隐（没有邻居可融合，渐隐只会白降权重）
edges = [b for b in plan2.boxes if b.y0 == 0 and b.x0 == 0][0]
we = tiling.box_weight(256, 64, edges)
check("贴图边的块：该侧权重保持 1（不无谓渐隐）", float(we[0, 128]) == 1.0,
      f"got {float(we[0, 128])}")

# ===========================================================================
print("\n=== 4. 极端尺寸与鲁棒性 ===\n")

for h, w in [(256, 256), (257, 256), (256, 257), (1, 800), (800, 1), (3, 3), (1000, 7),
             (64, 64), (65, 65)]:
    p = plan_tiles(h, w, tile=64, overlap=16)
    out = run_tiled(infer_pixelwise, gradient_src(h, w), p, feather_px=16)
    ok = out.shape == (3, h * SCALE, w * SCALE)
    finite = bool(np.isfinite(out).all())
    check(f"{h}x{w}：输出 {out.shape[1]}x{out.shape[2]}、无 NaN", ok and finite,
          f"shape={out.shape} finite={finite}")

p = plan_tiles(3, 3, tile=64, overlap=16)
out = run_tiled(infer_pixelwise, gradient_src(3, 3), p, feather_px=16)
check("小于 tile 的图：pad 区域不写回结果（输出仍是 6x6）",
      out.shape == (3, 6, 6), f"got {out.shape}")

# ===========================================================================
print("\n=== 5. 预处理 to_tensor ===\n")

img = Image.new("RGB", (64, 32))
img.putdata([(i % 256, (i * 3) % 256, (i * 7) % 256) for i in range(64 * 32)])
buf = io.BytesIO()
img.save(buf, "PNG")
png_bytes = buf.getvalue()

t, meta = to_tensor(png_bytes)
check("张量形状 (1,3,H,W)", t.shape == (1, 3, 32, 64), f"got {t.shape}")
check("默认 dtype float32", t.dtype == np.float32, f"got {t.dtype}")
check("默认归一化 /255：值域 [0,1]", float(t.min()) >= 0.0 and float(t.max()) <= 1.0)
check("meta 记录原图尺寸与格式",
      meta.size == (64, 32) and meta.format == "PNG" and meta.mode == "RGB")
check("无 alpha 时 meta.alpha 为 None", meta.alpha is None)

# 通道顺序
tb, _ = to_tensor(png_bytes, PreprocessSpec(channel_order="bgr"))
check("channel_order=bgr：通道 0 与 RGB 的通道 2 相同（红蓝互换）",
      float(np.abs(tb[0, 0] - t[0, 2]).max()) == 0.0)

# 归一化系数与 mean/std
th, _ = to_tensor(png_bytes, PreprocessSpec(scale=1.0))
check("scale=1.0：值域为 [0,255]", float(th.max()) > 1.0, f"max={float(th.max())}")
tn, _ = to_tensor(png_bytes, PreprocessSpec(scale=1.0 / 255.0, mean=(0.5, 0.5, 0.5),
                                           std=(0.5, 0.5, 0.5)))
check("mean/std 标准化：(0.5 灰度 → 0)", abs(float(tn[0, 0, 0, 0]) - (t[0, 0, 0, 0] - 0.5) / 0.5) < 1e-6)
t16, _ = to_tensor(png_bytes, PreprocessSpec(dtype="float16"))
check("dtype=float16 生效", t16.dtype == np.float16, f"got {t16.dtype}")

for bad, why in [
    (dict(channel_order="xyz"), "非法 channel_order"),
    (dict(dtype="int8"), "非法 dtype"),
    (dict(mean=(1, 1, 1)), "只给 mean 不给 std"),
]:
    try:
        PreprocessSpec(**bad)
        check(f"非法 spec 被拒（{why}）", False, "未抛错")
    except ValueError:
        check(f"非法 spec 被拒（{why}）", True)

# alpha / 灰度 / 调色板
rgba = Image.new("RGBA", (40, 20), (10, 20, 30, 128))
b2 = io.BytesIO()
rgba.save(b2, "PNG")
ta, ma = to_tensor(b2.getvalue())
check("RGBA：张量仍是 3 通道", ta.shape == (1, 3, 20, 40), f"got {ta.shape}")
check("RGBA：alpha 被单独留存", ma.alpha is not None and ma.alpha.shape == (20, 40)
      and ma.has_alpha and int(ma.alpha[0, 0]) == 128)

gray = Image.new("L", (30, 30), 100)
b3 = io.BytesIO()
gray.save(b3, "PNG")
tg, mg = to_tensor(b3.getvalue())
check("灰度 L：升为 3 通道且三通道同值",
      tg.shape == (1, 3, 30, 30) and float(np.abs(tg[0, 0] - tg[0, 1]).max()) == 0.0)

pal = Image.new("P", (16, 16))
b4 = io.BytesIO()
pal.save(b4, "PNG")
tp, mp = to_tensor(b4.getvalue())
check("调色板 P：转为 3 通道", tp.shape == (1, 3, 16, 16), f"got {tp.shape}")

# EXIF 方向
exif_img = Image.new("RGB", (100, 50), (200, 10, 10))
ex = Image.Exif()
ex[0x0112] = 6  # Orientation = 6 → 顺时针 90°，宽高应互换
b5 = io.BytesIO()
exif_img.save(b5, "JPEG", exif=ex)
te, me = to_tensor(b5.getvalue())
check("EXIF Orientation=6：尺寸被转正（100x50 → 50x100）", me.size == (50, 100), f"got {me.size}")
check("EXIF 转正后张量形状同步", te.shape == (1, 3, 100, 50), f"got {te.shape}")

try:
    to_tensor(b"not an image at all")
    check("损坏文件被拒（完整解码失败）", False, "未抛错")
except Exception:
    check("损坏文件被拒（完整解码失败）", True)

# ===========================================================================
print("\n=== 6. 后处理 from_tensor ===\n")

t2, meta2 = to_tensor(png_bytes)
back = from_tensor(np.repeat(np.repeat(t2, 4, axis=2), 4, axis=3), scale=4, meta=meta2)
check("输出尺寸 = 原图 × scale", back.size == (256, 128), f"got {back.size}")
check("输出模式 RGB", back.mode == "RGB", f"got {back.mode}")
check("往返后像素一致（最近邻放大不改变取值）",
      np.asarray(back)[0, 0].tolist() == np.asarray(img)[0, 0].tolist())

# 输出尺寸与期望不一致时对齐
bigger = np.zeros((1, 3, 140, 280), dtype=np.float32)
check("模型输出偏大：居中裁剪到目标尺寸",
      from_tensor(bigger, scale=4, meta=meta2).size == (256, 128))
smaller = np.zeros((1, 3, 100, 200), dtype=np.float32)
check("模型输出偏小：放大到目标尺寸",
      from_tensor(smaller, scale=4, meta=meta2).size == (256, 128))

# alpha 回填
ta2, meta_a = to_tensor(b2.getvalue())
ra = from_tensor(np.repeat(np.repeat(ta2, 2, axis=2), 2, axis=3), scale=2, meta=meta_a)
check("原图带 alpha → 输出 RGBA", ra.mode == "RGBA", f"got {ra.mode}")
check("alpha 被放大到结果尺寸", np.asarray(ra)[..., 3].shape == (40, 80),
      f"got {np.asarray(ra)[..., 3].shape}")
check("alpha 用最近邻放大：取值不变", int(np.asarray(ra)[0, 0, 3]) == 128)

check("clamp=True：超范围值被钳到 [0,1]",
      np.asarray(from_tensor(np.full((1, 3, 4, 4), 3.0, np.float32), scale=1,
                             meta=ImageMeta((4, 4), "RGB", "PNG", None)))[0, 0, 0].item() == 255)
clipped = from_tensor(np.full((1, 3, 4, 4), -5.0, np.float32), scale=1,
                      meta=ImageMeta((4, 4), "RGB", "PNG", None))
check("clamp=True：负值被钳到 0", np.asarray(clipped)[0, 0, 0].item() == 0)

# 编码
out_img = Image.fromarray(np.full((8, 8, 3), 128, dtype=np.uint8), "RGB")
roundtrip = Image.open(io.BytesIO(encode_image(out_img, "PNG")))
check("PNG 无损编码往返一致", np.array_equal(np.asarray(roundtrip), np.asarray(out_img)))
jpeg = Image.open(io.BytesIO(encode_image(out_img, "JPEG")))
check("JPEG 编码可解码且尺寸一致", jpeg.size == (8, 8))
rgba_enc = encode_image(Image.new("RGBA", (4, 4), (1, 2, 3, 4)), "JPEG")
check("RGBA 图编码 JPEG 时自动去 alpha 不报错", len(rgba_enc) > 0)

try:
    from_tensor(np.zeros((2, 3, 8, 8), np.float32), scale=1, meta=meta2)
    check("batch != 1 被拒", False, "未抛错")
except ValueError:
    check("batch != 1 被拒", True)
try:
    from_tensor(np.zeros((1, 4, 8, 8), np.float32), scale=1, meta=meta2)
    check("通道数 != 3 被拒", False, "未抛错")
except ValueError:
    check("通道数 != 3 被拒", True)

# ===========================================================================
print("\n=== 7. 累加器契约 ===\n")

acc = TileAccumulator(plan2, scale=SCALE, feather_px=64)
bad_cases = [
    (np.zeros((1, 3, 100, 100), np.float32), "块尺寸不匹配"),
    (np.zeros((2, 3, 512, 512), np.float32), "batch != 1"),
    (np.zeros((1, 4, 512, 512), np.float32), "通道数不匹配"),
]
for arr, why in bad_cases:
    try:
        acc.add(plan2.boxes[0], arr)
        check(f"累加器拒绝非法块（{why}）", False, "未抛错")
    except ValueError:
        check(f"累加器拒绝非法块（{why}）", True)

check("added 计数随累加增长", acc.added == 0)
acc.add(plan2.boxes[0], np.zeros((1, 3, 512, 512), np.float32))
check("added 计数 +1（供 M2 上报 chunk 进度）", acc.added == 1)

try:
    stitch(plan2, [np.zeros((1, 3, 512, 512), np.float32)], scale=SCALE, feather_px=64)
    check("stitch 结果数量不足被拒", False, "未抛错")
except ValueError:
    check("stitch 结果数量不足被拒", True)

ok = stitch(plan2, [np.full((1, 3, 512, 512), 0.3, np.float32) for _ in plan2.boxes],
            scale=SCALE, feather_px=64)
check("stitch 纯函数入口可用（结果数匹配）",
      ok.shape == (3, 2000, 1600) and float(np.abs(ok - 0.3).max()) <= 1e-6)

# ===========================================================================
print(f"\n{'=' * 60}")
print(f"T-802 验证结果：{PASS} 通过 / {FAIL} 失败")
print("=" * 60)
sys.exit(1 if FAIL else 0)
