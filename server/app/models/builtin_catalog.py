"""内置模型目录（M3 登记来源之一）。

设计决策（STEP-5D §T-604）：
- 只登记**产品认可的内置模型**；`data/models/` 下的基准变体
  （`RealESRGAN_x4_fp16all.onnx` / `RealESRGAN_x4_s512.onnx` / IR fp32 对）
  是开发期基准产物，**不进入产品模型库**；
- 元信息（架构 / 许可证 / 最低显存 / 能力声明）来源于前置调研与 P0 实测
  （docs/tech/research/），属模型固有属性，不是"单机实测数值"，可以声明；
- `capabilities.supports_tile0` 依据 `data/models/inspect.json` 的
  `has_dynamic_hw: true`（RealESRGAN_x4 动态 H/W 已验证）；
- 文件缺失时跳过并告警（允许裁剪分发），不阻断启动。
- **风格由「模型」区分，不由参数区分**（PRD §7.3：模型不做自动推荐，选择权在用户）：
  通用 / 写实 = `RealESRGAN_x4plus`，动漫 = `RealESRGAN_x4plus_anime_6B`，
  轻量通用 = `realesr-general-x4v3`。后两者由 `tools/convert_to_onnx.py` 离线转换得到，
  动态 H/W 与正确性由转换产物的 `dynamic: true` 与**同源自检**保证
  （anime_6B 3.22e-06 / general-x4v3 4.08e-06，阈值 1e-3）。
- **description = 适配简介**（T-716）：统一口径「一句话定位 + 适合场景 + 不适合/注意」，
  是帮助用户选择的策展文案（模型固有属性），不是运行时推荐。
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class BuiltinModelSpec:
    path: str  # 相对 data/models/ 的路径
    name: str
    architecture: str
    description: str
    format: str  # MODEL_FORMATS
    scale: int
    license: str
    min_vram_mb: int
    supported_backends: list[str]
    params_count: int | None = None
    companion_name: str | None = None  # 同目录下的配套文件名（IR 的 .bin）
    capabilities: dict = field(default_factory=dict)


_DEFAULT_CAPS = {
    "supports_fp16": True,
    "supports_batch": False,
    "supports_tile0": True,
    "has_tensorrt": False,
    "is_generative": False,
    "num_inference_steps": None,
    "requires_prompt": False,
}

BUILTIN_MODELS: tuple[BuiltinModelSpec, ...] = (
    BuiltinModelSpec(
        path="RealESRGAN_x4.onnx",
        name="RealESRGAN_x4plus",
        architecture="RRDBNet",
        description="通用 4 倍超分主模型。适合写实照片：人像、风景、建筑、日常拍摄，"
                    "对老照片翻新、低清素材放大同样稳定；处理动漫截图请选动漫特化模型。",
        format="onnx",
        scale=4,
        license="BSD-3-Clause",
        min_vram_mb=4096,
        supported_backends=["cuda", "cpu", "openvino"],
        params_count=16_697_987,
        capabilities=dict(_DEFAULT_CAPS),
    ),
    BuiltinModelSpec(
        path="RealESRGAN_x4_fp16.onnx",
        name="RealESRGAN_x4plus_fp16",
        architecture="RRDBNet",
        description="上者的 fp16 半精度导出，适用场景完全相同（写实照片 4 倍放大）。"
                    "体积减半，在支持 fp16 的后端上吞吐约为 fp32 的 2 倍；"
                    "显存紧张或追求速度时优先选它。",
        format="onnx",
        scale=4,
        license="BSD-3-Clause",
        min_vram_mb=2048,
        supported_backends=["cuda", "cpu"],
        params_count=16_697_987,
        capabilities=dict(_DEFAULT_CAPS),
    ),
    BuiltinModelSpec(
        path="ir/RealESRGAN_x4_fp16.xml",
        name="RealESRGAN_x4 (OpenVINO IR)",
        architecture="RRDBNet",
        description="面向 Intel 核显 / 集显机器的通用 4 倍超分。适合没有独立显卡的轻薄本、"
                    "办公机做写实照片放大；走 OpenVINO 原生 API，不经过 ORT，纯 CPU 也能跑。",
        format="openvino_ir",
        scale=4,
        license="BSD-3-Clause",
        min_vram_mb=0,
        supported_backends=["openvino", "cpu"],
        params_count=16_697_987,
        companion_name="RealESRGAN_x4_fp16.bin",
        capabilities=dict(_DEFAULT_CAPS),
    ),
    # ---- 风格模型（2026-10-09 增补，用户确认「补动漫 + 轻量通用」）----
    # 二者与原主模型同为 Real-ESRGAN 家族，但**是不同的权重**（不是同一模型的技术变体）：
    # 动漫 6 块 RRDB（4.47M 参数）面向线条与平涂色块；general-x4v3（1.21M 参数）
    # 是 SRVGGNetCompact 轻量网络。均由官方权重经离线转换得到（BSD-3-Clause）。
    BuiltinModelSpec(
        path="RealESRGAN_x4plus_anime_6B.onnx",
        name="RealESRGAN_x4plus_anime_6B",
        architecture="RRDBNet",
        description="动漫特化 4 倍超分（RRDBNet 6 块，4.47M 参数）。适合动画截图、插画、漫画等"
                    "以线条和平涂色块为主的素材，保得住线条锐度；处理写实照片请选通用模型。",
        format="onnx",
        scale=4,
        license="BSD-3-Clause",
        min_vram_mb=2048,
        supported_backends=["cuda", "cpu", "openvino"],
        params_count=4_467_779,
        capabilities=dict(_DEFAULT_CAPS),
    ),
    BuiltinModelSpec(
        path="realesr-general-x4v3.onnx",
        name="realesr-general-x4v3",
        architecture="SRVGGNetCompact",
        description="轻量通用 4 倍超分（SRVGGNetCompact，1.21M 参数、4.6 MB）。适合低配电脑、"
                    "纯 CPU / 核显机器，以及批量处理大量图片求快的场景；速度比 x4plus 快一个量级，"
                    "细节还原能力弱于 x4plus。",
        format="onnx",
        scale=4,
        license="BSD-3-Clause",
        min_vram_mb=1024,
        supported_backends=["cuda", "cpu", "openvino"],
        params_count=1_213_296,
        capabilities=dict(_DEFAULT_CAPS),
    ),
    # ---- 多倍率补齐（2026-10-10 增补，用户确认「引入 2x/3x 模型」）----
    # 倍率烧在网络结构里：此前内置 5 项全是 ×4，倍率 2/3 恒置灰（资产缺口非能力缺口）。
    # ×2 选官方同家族通用模型；×3 在 Real-ESRGAN 家族无官方权重，选 Real-CUGAN
    # （B 站官方，动漫向，MIT）。均由 tools/convert_to_onnx.py 离线转换并同源自检：
    # x2plus 1.97e-06 / up3x 3.81e-06（阈值 1e-3）。
    # ⚠️ up3x 的 ONNX 为动态导出，追踪期把尺寸分支按对齐探针烘焙——产物仅对
    # **4 的倍数**输入保证正确（引擎 tile 恒按 DEFAULT_ALIGN=8 补齐，链路安全）。
    BuiltinModelSpec(
        path="RealESRGAN_x2plus.onnx",
        name="RealESRGAN_x2plus",
        architecture="RRDBNet",
        description="通用 2 倍超分（RRDBNet，16.7M 参数）。与 x4plus 同家族同架构的官方 2 倍"
                    "权重，适合图片本身已较清晰、只需轻度放大到目标分辨率（2K/4K）或放大后还要"
                    "手动精修的场景；幅度温和、细节保留更好。",
        format="onnx",
        scale=2,
        license="BSD-3-Clause",
        min_vram_mb=4096,
        supported_backends=["cuda", "cpu", "openvino"],
        params_count=16_703_171,
        capabilities=dict(_DEFAULT_CAPS),
    ),
    BuiltinModelSpec(
        path="RealCUGAN_up3x.onnx",
        name="RealCUGAN_up3x",
        architecture="RealCUGAN",
        description="动漫特化 3 倍超分（RealCUGAN，1.29M 参数、4.9 MB，B 站官方无降噪版本）。"
                    "适合动画截图与插画的 3 倍放大，线条干净、色块平整；处理写实照片请选通用模型。",
        format="onnx",
        scale=3,
        license="MIT",
        min_vram_mb=1024,
        supported_backends=["cuda", "cpu", "openvino"],
        params_count=1_286_326,
        capabilities=dict(_DEFAULT_CAPS),
    ),
)
