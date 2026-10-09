"""模型库 DTO（对齐 web/src/types/api.ts 的 Model / ModelCapabilities）。

序列化差异集中说明（均在 STEP-5D §T-604 登记，冻结核对归 T-700）：
- DB `param_count` → API `params_count`（契约命名）；
- DB 无 architecture / description / capabilities 列 —— 由服务层合成
  （内置模型查 catalog，导入模型按格式给默认值）；
- DB `companion_path`（单一相对路径）→ API `companion`（扩展名数组，
  形如 [".xml", ".bin"]），与前端已定稿类型一致；
- 契约 §3.2 的 `size_bytes` 不落库，响应时由文件 stat 合成。
"""
from pydantic import BaseModel


class ModelCapabilities(BaseModel):
    supports_fp16: bool
    supports_batch: bool
    supports_tile0: bool
    has_tensorrt: bool
    is_generative: bool
    num_inference_steps: int | None
    requires_prompt: bool


class ModelConversion(BaseModel):
    """`.pth` / `.safetensors` 的离线转换可用性（T-807）。

    非这两种格式时 `ModelOut.conversion` 为 `null`——"不需要转换"与
    "需要但环境没装"是两回事，用一个可空对象表达而不是两个布尔。
    """

    available: bool
    reason: str | None


class ModelOut(BaseModel):
    id: str
    name: str
    architecture: str
    description: str
    format: str
    path: str
    sha256: str
    size_bytes: int
    params_count: int | None
    scale: int | None
    license: str | None
    source: str
    min_vram_mb: int | None
    supported_backends: list[str]
    capabilities: ModelCapabilities
    companion: list[str] | None
    available: bool
    unavailable_reason: str | None
    # 前端类型无此字段，但列表置灰/「需先转换」提示需要状态语义；
    # 是否进契约列入 T-700 冻结核对（当前前端忽略未知字段，无破坏）
    status: str
    # T-807：仅 .pth / .safetensors 有值；其它格式为 null
    conversion: ModelConversion | None = None
