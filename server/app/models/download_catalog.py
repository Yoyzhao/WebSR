"""可下载模型目录（T-713）。

定位与边界：

- 这是**产品认可的第三方模型白名单**：每一条都经过开发机实测（下载 → spandrel 识别 →
  ONNX 转换 → 同源自检通过）才允许进目录，**不做"看起来像就能下"的猜测**——
  与 ADR-003「无法识别则明确报错」同源。
- **效果优先排序**：`effect_rank` 越小越靠前，是人工策展序（社区公认效果 + 场景覆盖），
  **不是运行时推荐**——PRD §7.3「模型始终由用户选择」不变，这里只提供"可获取"的清单。
- 全部条目为**直链**（GitHub Releases），应用内不搭理需要网页授权的来源
  （Google Drive 等），避免"按钮点了却下不动"的体验缺口。
- **实测落选记录**（2026-10-10，避免以后重复踩）：`4xNomos2_hq_dat2`（DAT，窗口注意力
  Reshape 形状被追踪烘焙 → 动态导出仅探针尺寸正确）；`4xNomosWebPhoto_atd`（ATD 用
  `torch.sort` → ONNX 导出直接不支持）。二者都**不进目录**——"能转换"不等于"转换后正确"。
- **`scale=1` 是修复类，不是"放大 1 倍"**（T-717）：输出尺寸等于输入尺寸，只处理退化。
  三条分工明确且**必须写进简介**：`DeNoise` 降噪 / `DeJPG` 去静态 JPEG 痕 /
  `DeH264` 去视频编码痕。UI 侧倍率标签显示为「×1 修复」，避免用户误以为能变大。
  ⚠️ 人脸**专用重建**（GFPGAN / CodeFormer 一类）实测不可进目录：见本文件末尾
  `_REJECTED` 说明——1x 走"通用修复"路线，人像只是受益场景之一，不做人脸专属承诺。
- `.pth` 权重下载后**自动进入应用内转换**（`conversion_service`，ADR-003 子进程），
  产物登记为新模型；转换环境缺失时登记为 `needs_convert` 态（既有三级判定管后续）。
- `min_vram_mb` 是**可用性门槛**（保守声明，非实测数值），与内置模型口径一致。
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class DownloadableModelSpec:
    id: str                 # 稳定目录 id（不入库，只作目录键）
    name: str               # 登记用模型名
    scale: int              # 1（修复）/ 2 / 3 / 4
    architecture: str       # 展示用架构名
    style: str              # 场景标签：通用照片 / 动漫 / 修复
    license: str
    size_bytes: int         # 权重文件大小（用于下载前告知与进度）
    url: str                # 直链（GitHub Releases）
    filename: str           # 保存文件名（.pth）
    effect_rank: int        # 效果优先序（1 最强）；同分按体积升序
    min_vram_mb: int        # 可用性门槛（保守声明）
    description: str
    quality_note: str       # 效果说明（策展理由，中文）


DOWNLOAD_CATALOG: tuple[DownloadableModelSpec, ...] = (
    DownloadableModelSpec(
        id="dl_4x_nomoswebphoto_esrgan",
        name="4xNomosWebPhoto_esrgan",
        scale=4,
        architecture="ESRGAN",
        style="通用照片",
        license="CC-BY-4.0",
        size_bytes=33_714_407,
        url="https://github.com/Phhofm/models/releases/download/4xNomosWebPhoto_esrgan/4xNomosWebPhoto_esrgan.pth",
        filename="4xNomosWebPhoto_esrgan.pth",
        effect_rank=1,
        min_vram_mb=4096,
        description="网络压缩照片特化 4 倍超分（RRDBNet 23 块）。适合截图、聊天图片、网页图"
                    "等带 JPEG 压缩痕的网络来源图片，去压缩痕效果突出。",
        quality_note="对网络来源的压缩图（截图、聊天图片、网页图）去压缩痕效果突出，算力需求适中。",
    ),
    DownloadableModelSpec(
        id="dl_2x_nomosuni_esrgan_multijpg",
        name="2xNomosUni_esrgan_multijpg",
        scale=2,
        architecture="ESRGAN",
        style="通用照片",
        license="CC-BY-4.0",
        size_bytes=33_701_468,
        url="https://github.com/Phhofm/models/releases/download/2xNomosUni_esrgan_multijpg/2xNomosUni_esrgan_multijpg.pth",
        filename="2xNomosUni_esrgan_multijpg.pth",
        effect_rank=2,
        min_vram_mb=4096,
        description="通用照片 2 倍超分（RRDBNet，16.7M 参数，×2 里画质最强的一档）。"
                    "适合本来就只想放大一档的场景：人像、日常照片、含 JPEG 压缩痕的网络图；"
                    "训练集含人像，对人脸细节的还原好于轻量模型。代价是体积大、耗时高。",
        quality_note="×2 里的画质天花板：细节与纹理还原最扎实，代价是 67MB 体积与较高耗时。",
    ),
    DownloadableModelSpec(
        id="dl_2x_animesharp_v3",
        name="2x-AnimeSharpV3",
        scale=2,
        architecture="ESRGAN",
        style="动漫",
        license="CC-BY-NC-SA-4.0",
        size_bytes=67_108_869,
        url="https://github.com/Kim2091/Kim2091-Models/releases/download/2x-AnimeSharpV3/2x-AnimeSharpV3.pth",
        filename="2x-AnimeSharpV3.pth",
        effect_rank=3,
        min_vram_mb=4096,
        description="动漫 2 倍超分（Kim2091 系列 V3，社区使用最广的动漫放大模型之一）。"
                    "适合高清化动画截图与插画：线稿锐利、色块干净；注意 NC 授权（非商用）。",
        quality_note="动漫线稿锐利、色块干净；注意 NC 授权（非商用）。",
    ),
    DownloadableModelSpec(
        id="dl_1x_denoise_realplksr_otf",
        name="1xDeNoise_realplksr_otf",
        scale=1,
        architecture="PLKSR",
        style="修复",
        license="CC-BY-4.0",
        size_bytes=29_559_554,
        url="https://github.com/Phhofm/models/releases/download/1xDeNoise_realplksr_otf/1xDeNoise_realplksr_otf.pth",
        filename="1xDeNoise_realplksr_otf.pth",
        effect_rank=4,
        min_vram_mb=2048,
        description="1 倍修复（去噪专用，输出尺寸不变）。适合高感光度/夜景照片的噪点、老照片颗粒感、"
                    "轻度模糊；也适用于含人脸的写实照片——人脸会跟着变干净，但它是通用修复，"
                    "不是人脸专用重建。想去掉块状压缩痕请选 1xDeJPG。",
        quality_note="不改变尺寸，只去噪点：噪点多的夜景/老照片提升最明显，附带少量压缩痕处理。",
    ),
    DownloadableModelSpec(
        id="dl_1x_dejpg_realplksr_otf",
        name="1xDeJPG_realplksr_otf",
        scale=1,
        architecture="PLKSR",
        style="修复",
        license="CC-BY-4.0",
        size_bytes=29_559_554,
        url="https://github.com/Phhofm/models/releases/download/1xDeJPG_realplksr_otf/1xDeJPG_realplksr_otf.pth",
        filename="1xDeJPG_realplksr_otf.pth",
        effect_rank=5,
        min_vram_mb=2048,
        description="1 倍修复（去 JPEG 压缩痕专用，输出尺寸不变）。适合被反复保存/转发过的图："
                    "块状噪点、文字与边缘发虚、色彩断层。与 1xDeNoise 是分工关系——"
                    "噪点选前者、压缩痕选本条；两者都能让照片在不放大尺寸的前提下变清晰。",
        quality_note="专治块状压缩痕与边缘发虚：转发过多次的图提亮最明显，附带轻量降噪。",
    ),
    DownloadableModelSpec(
        id="dl_1x_deh264_realplksr",
        name="1xDeH264_realplksr",
        scale=1,
        architecture="PLKSR",
        style="修复",
        license="CC-BY-4.0",
        size_bytes=29_559_554,
        url="https://github.com/Phhofm/models/releases/download/1xDeH264_realplksr/1xDeH264_realplksr.pth",
        filename="1xDeH264_realplksr.pth",
        effect_rank=6,
        min_vram_mb=2048,
        description="1 倍修复（去视频压缩痕专用，输出尺寸不变）。适合录屏、视频截图、直播截图"
                    "这类带 h264/AVC 编码块与色度渗色的图——和 1xDeJPG 是分工关系："
                    "图片被反复存成 JPEG 选 1xDeJPG，来自视频编码的选本条。",
        quality_note="专治录屏与视频截图的编码块、色度渗色；静态 JPEG 压缩痕仍选 1xDeJPG。",
    ),
    DownloadableModelSpec(
        id="dl_4x_nomos2_hq_mosr",
        name="4xNomos2_hq_mosr",
        scale=4,
        architecture="MoSR",
        style="通用照片",
        license="CC-BY-4.0",
        size_bytes=17_177_457,
        url="https://github.com/Phhofm/models/releases/download/4xNomos2_hq_mosr/4xNomos2_hq_mosr.pth",
        filename="4xNomos2_hq_mosr.pth",
        effect_rank=7,
        min_vram_mb=2048,
        description="通用照片 4 倍超分（MoSR 轻量架构）。适合想兼顾画质与速度的日常照片放大："
                    "观感接近重型模型，算力需求低一个量级，中低配机器也能流畅跑。",
        quality_note="轻量档里效果最好的一档：接近重型模型的观感，算力需求低一个量级。",
    ),
    DownloadableModelSpec(
        id="dl_2x_nomosuni_compact_otf_medium",
        name="2xNomosUni_compact_otf_medium",
        scale=2,
        architecture="RealESRGAN Compact",
        style="通用照片",
        license="CC-BY-4.0",
        size_bytes=2_419_158,
        url="https://github.com/Phhofm/models/releases/download/2xNomosUni_compact_otf_medium/2xNomosUni_compact_otf_medium.pth",
        filename="2xNomosUni_compact_otf_medium.pth",
        effect_rank=8,
        min_vram_mb=1024,
        description="通用照片 2 倍超分（Compact 轻量网络，2.3 MB）。一模型覆盖去压缩痕、降噪、"
                    "去模糊三类常见退化，适合低配电脑、纯 CPU、批量处理；画质轻于上面的重型款，"
                    "但速度快一个量级。",
        quality_note="2MB 级的通用 ×2：去压缩/降噪/去模糊一把抓，低配与纯 CPU 也能流畅跑。",
    ),
    DownloadableModelSpec(
        id="dl_2x_hfa2k_avc_compact",
        name="2xHFA2kAVCCompact",
        scale=2,
        architecture="RealESRGAN Compact",
        style="动漫",
        license="CC-BY-4.0",
        size_bytes=1_219_249,
        url="https://github.com/Phhofm/models/releases/download/2xHFA2kAVCCompact/2xHFA2kAVCCompact.pth",
        filename="2xHFA2kAVCCompact.pth",
        effect_rank=9,
        min_vram_mb=1024,
        description="动漫 2 倍超分（Compact 轻量，专治 h264/AVC 视频压缩退化）。适合从录屏、"
                    "在线视频里截出来的动画画面——这类图带块状压缩痕，用 AnimeSharpV3 反而会"
                    "把压缩痕一起锐化；干净截图仍首选 AnimeSharpV3。",
        quality_note="录屏/在线视频截图专用：先吃掉压缩块再放大；干净截图仍选 AnimeSharpV3。",
    ),
    DownloadableModelSpec(
        id="dl_2x_ani4k_compact",
        name="2x_Ani4K_Compact",
        scale=2,
        architecture="SRVGGNetCompact",
        style="动漫",
        license="CC-BY-NC-4.0",
        size_bytes=4_792_701,
        url="https://github.com/Sirosky/Upscale-Hub/releases/download/Ani4K/2x_Ani4K_Compact_35000.pth",
        filename="2x_Ani4K_Compact_35000.pth",
        effect_rank=10,
        min_vram_mb=1024,
        description="动漫 2 倍超分（Compact 轻量网络，面向 4K 观看目标的实时级体积）。"
                    "适合低配电脑、纯 CPU 场景下放大动画截图与插画；速度极快，效果轻于 AnimeSharpV3。",
        quality_note="5MB 级动漫轻量模型，低配与纯 CPU 也能跑；效果轻于 AnimeSharpV3。",
    ),
)


def get_entry(entry_id: str) -> DownloadableModelSpec | None:
    for e in DOWNLOAD_CATALOG:
        if e.id == entry_id:
            return e
    return None


# ---------------------------------------------------------------------------
# 实测落选记录（T-717，2026-10-11）：人脸**专用重建**类不进目录
#
# `GFPGANv1.4`（TencentARC，348 MB）三重不可行，逐条都有实测产物为证：
#   1) 动态导出失败 —— StyleGAN2 的 `ModulatedConv2d` 用 Reshape 拼动态卷积核，
#      TorchScript 追踪后核形状未知 →
#      "ONNX export of convolution for kernel of unknown shape"。
#   2) 静态 512 导出虽能落盘，但 ONNX Runtime 拒绝执行 →
#      "Could not find an implementation for Conv(11) node
#      '/inner/stylegan_decoder/to_rgb1/modulated_conv/Conv'"。
#   3) 即便能跑，GFPGAN 的输入是**对齐后的人脸裁剪**（固定 512），需要
#      「检测 → 对齐 → 重建 → 贴回」四段管线，本项目没有该链路
#      （PRD 把「人脸修复」排在项目 S3）。
#
# 结论：×1 目录只提供**通用修复**（去噪 / 去 JPEG / 去 H264），人像只是受益
# 场景之一，**不做"人脸专属重建"的承诺**——承诺了却交付不了比不承诺更糟。
# CodeFormer 同族（同样依赖 GAN 解码器），不再重复实测。
# ---------------------------------------------------------------------------
_REJECTED: tuple[str, ...] = (
    "GFPGANv1.4（StyleGAN2 调制卷积：动态导出失败 + 静态导出 ORT 未实现 + 缺人脸对齐管线）",
)
