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
)
