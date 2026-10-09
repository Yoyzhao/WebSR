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
        description="通用 4 倍超分，对人像、建筑、风景均有稳定表现，是内置的默认主模型。",
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
        description="上者的 fp16 导出，体积减半，在支持 fp16 的后端上吞吐约为 fp32 的 2 倍。",
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
        description="面向 Intel 集显 / 核显的 IR 格式，走 OpenVINO 原生 API，不经过 ORT。",
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
        description="动漫特化 4 倍超分（RRDBNet 6 块，4.47M 参数）。针对线条与平涂色块优化，"
                    "适用于动画截图与插画；处理写实照片请选通用模型。",
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
        description="轻量通用 4 倍超分（SRVGGNetCompact，1.21M 参数、4.6 MB）。体积与算力需求"
                    "远低于 x4plus，面向低配或纯 CPU 场景；细节还原能力弱于 x4plus。",
        format="onnx",
        scale=4,
        license="BSD-3-Clause",
        min_vram_mb=1024,
        supported_backends=["cuda", "cpu", "openvino"],
        params_count=1_213_296,
        capabilities=dict(_DEFAULT_CAPS),
    ),
)
