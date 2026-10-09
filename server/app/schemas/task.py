"""任务中心 DTO（对齐 web/src/types/api.ts 的 Task / Progress / Artifact）。

差异登记（STEP-5D §T-606，冻结核对归 T-700）：
- chunk 级进度只存在于运行期内存（DB 只落 item 级），序列化时合并；
- `file_id` 快照在 `params.file_id`（请求体的一部分），序列化提升为顶层字段；
- `filename` / `source_width` / `source_height` 由上传旁车元信息解析（T-605）；
- `output_width/height` / `artifacts` 由 T-808 真实产物回填，当前恒 null / []。
"""
from pydantic import BaseModel


class ProgressOut(BaseModel):
    percent: float
    current_item: int
    total_items: int
    current_chunk: int
    total_chunks: int


class TaskParamsIn(BaseModel):
    scale: int
    model_id: str
    tile: int | None
    precision: str | None  # 'fp32' | 'fp16' | None（自动档）
    backend: str | None
    auto: bool


class CreateTaskIn(BaseModel):
    type: str
    file_id: str
    params: TaskParamsIn


class ArtifactOut(BaseModel):
    id: str
    task_id: str
    kind: str
    path: str
    filename: str
    width: int | None = None
    height: int | None = None
    size_bytes: int | None
    sha256: str | None
    created_at: str


class TaskOut(BaseModel):
    id: str
    type: str
    status: str
    progress: ProgressOut
    params: dict
    resolved: dict | None
    error: dict | None
    model_id: str | None
    model_name: str | None
    file_id: str | None
    filename: str | None
    source_width: int | None
    source_height: int | None
    output_width: int | None
    output_height: int | None
    duration_ms: int | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    artifacts: list[ArtifactOut] = []
    ep_evidence: list[dict] = []
