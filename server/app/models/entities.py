"""6 张业务表（tech-arch §4.1 ER + §4.2 设计说明）。

类型策略（ADR-006 §C-11，违反即返工）：
- 枚举字段用 `String`，**DB 层不设 CHECK**（取值会增长，如 canceling / interrupted
  均为后期补入）；取值约束放 Python 层（下方常量 + 校验层），与前端已定稿类型
  `web/src/types/api.ts` 保持一致；
- 结构字段（params / resolved / supported_backends / tile_curve …）用 `JSON` 列，
  `supported_backends` 必须是 **JSON 数组**，不得用逗号分隔字符串；
- 时间字段一律 `UTCDateTime`（存 UTC，展示层转 Asia/Shanghai）。

与 ER 的两处实现级差异（已在 STEP-5D §T-602 登记）：
1. `TASK.error` 存**统一错误体 JSON 对象**（ER 图的 `error_message` 字符串是早期简写；
   契约 §3.1 的 `Task.error` 是对象，落 JSON 与 §4.2「结构字段用 JSON」一致）；
2. `ARTIFACT.kind` 取 ER 超集（input/output/thumb/log/model）；前端类型当前只有
   output/intermediate，属展示层子集，归并口径列入 T-700 冻结核对。
"""
from datetime import datetime, timezone

from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UTCDateTime

# ---- Python 层枚举取值（DB 不设 CHECK；新增取值改这里 + 校验层，无需迁移）----
TASK_TYPES = ("upscale", "batch_upscale", "face_restore", "video")
TASK_STATUSES = ("queued", "running", "canceling", "completed", "canceled", "failed", "interrupted")
TIERS = ("T0", "T1", "T2", "T3")
MODEL_FORMATS = ("onnx", "openvino_ir", "ncnn", "pth", "safetensors")
MODEL_STATUSES = ("ready", "needs_convert", "invalid")
MODEL_SOURCES = ("builtin", "imported")
ARTIFACT_KINDS = ("input", "output", "thumb", "log", "model")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Task(Base):
    __tablename__ = "task"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(32))
    # 协作式取消含中间态 canceling；重启回收为 interrupted（ADR-005 / tech-arch §6.6）
    status: Mapped[str] = mapped_column(String(16), default="queued")
    params: Mapped[dict] = mapped_column(JSON)  # 用户请求值（"自动"档为 null 占位）
    resolved: Mapped[dict | None] = mapped_column(JSON)  # 引擎决策结果（tile/精度/EP/理由）
    error: Mapped[dict | None] = mapped_column(JSON)  # 统一错误体对象（失败时填入）
    tier: Mapped[str | None] = mapped_column(String(8))  # 冗余存档位，便于统计排查
    model_id: Mapped[int | None] = mapped_column(ForeignKey("model.id", ondelete="SET NULL"))
    calibration_id: Mapped[int | None] = mapped_column(ForeignKey("calibration.id", ondelete="SET NULL"))
    progress_done: Mapped[int] = mapped_column(Integer, default=0)  # 以 item 计
    progress_total: Mapped[int] = mapped_column(Integer, default=1)  # chunk 级只走 SSE，不落库
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (
        # 任务中心 = 按状态筛选 + 按时间倒序分页（ADR-006 附录 B）
        Index(None, "status", "created_at"),
    )


class Artifact(Base):
    __tablename__ = "artifact"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ON DELETE CASCADE：删任务连带删产物记录（磁盘文件清理由业务层负责）
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    path: Mapped[str] = mapped_column(String(512))  # 相对 data/ 的路径（图片不存库）
    sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utcnow)

    __table_args__ = (Index(None, "task_id"),)


class Model(Base):
    __tablename__ = "model"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    format: Mapped[str] = mapped_column(String(16))  # MODEL_FORMATS
    companion_path: Mapped[str | None] = mapped_column(String(512))  # .bin 的 .xml/.param
    supported_backends: Mapped[list] = mapped_column(JSON)  # JSON 数组；生效后端只记 TASK.resolved
    path: Mapped[str] = mapped_column(String(512))
    sha256: Mapped[str] = mapped_column(String(64), unique=True)  # 导入去重（ADR-006 附录 B）
    param_count: Mapped[int | None] = mapped_column(BigInteger)
    scale: Mapped[int | None] = mapped_column(Integer)  # 2 / 3 / 4
    min_vram_mb: Mapped[int | None] = mapped_column(Integer)  # 可用性门槛（非排序依据）
    license: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16), default="imported")  # builtin | imported
    status: Mapped[str] = mapped_column(String(16), default="ready")  # ready | needs_convert | invalid
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utcnow)


class Preset(Base):
    __tablename__ = "preset"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    params: Mapped[dict] = mapped_column(JSON)
    tier: Mapped[str | None] = mapped_column(String(8))  # 绑定档位，空 = 通用
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)


class Setting(Base):
    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(String(2048))
    value_type: Mapped[str] = mapped_column(String(16), default="string")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utcnow, onupdate=_utcnow)


class Calibration(Base):
    __tablename__ = "calibration"

    id: Mapped[int] = mapped_column(primary_key=True)
    # tile 曲线是 per-(模型 × 硬件)：模型用外键，指纹只放硬件项（tech-arch §4.2）
    model_id: Mapped[int | None] = mapped_column(ForeignKey("model.id", ondelete="SET NULL"))
    hardware_fingerprint: Mapped[str] = mapped_column(String(256))  # gpu+driver+vram+ort+ov+cpu_isa
    tile_curve: Mapped[dict | None] = mapped_column(JSON)  # 每档 tile 的延迟与峰值
    precision_decision: Mapped[str | None] = mapped_column(String(16))
    recommended_tier: Mapped[str | None] = mapped_column(String(8))  # 推荐档位（非推荐模型）
    reason: Mapped[str | None] = mapped_column(String(1024))  # 决策理由（人类可读）
    valid: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utcnow)

    __table_args__ = (
        # 标定失效判据查询（ADR-006 附录 B）
        Index(None, "model_id", "hardware_fingerprint", "valid"),
    )
