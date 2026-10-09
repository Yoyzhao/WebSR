"""初始建表：task / artifact / model / preset / setting / calibration（tech-arch §4.1）

Revision ID: 0001
Revises:
Create Date: 2026-10-08

类型策略见 app/models/entities.py 模块 docstring（ADR-006 §C-10/C-11）。
自定义类型 UTCDateTime 的 import 为手工补充（ADR-006 约束 5）。
"""
from alembic import op
import sqlalchemy as sa

from app.models.base import UTCDateTime  # 约束 5：autogenerate 不补的 import 手工添加

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("resolved", sa.JSON(), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("tier", sa.String(length=8), nullable=True),
        sa.Column("model_id", sa.Integer(), nullable=True),
        sa.Column("calibration_id", sa.Integer(), nullable=True),
        sa.Column("progress_done", sa.Integer(), nullable=False),
        sa.Column("progress_total", sa.Integer(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("started_at", UTCDateTime(), nullable=True),
        sa.Column("finished_at", UTCDateTime(), nullable=True),
        sa.ForeignKeyConstraint(["model_id"], ["model.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["calibration_id"], ["calibration.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_status_created_at", "task", ["status", "created_at"])

    op.create_table(
        "artifact",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["task.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_artifact_task_id", "artifact", ["task_id"])

    op.create_table(
        "model",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("format", sa.String(length=16), nullable=False),
        sa.Column("companion_path", sa.String(length=512), nullable=True),
        sa.Column("supported_backends", sa.JSON(), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("param_count", sa.BigInteger(), nullable=True),
        sa.Column("scale", sa.Integer(), nullable=True),
        sa.Column("min_vram_mb", sa.Integer(), nullable=True),
        sa.Column("license", sa.String(length=64), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sha256"),
    )

    op.create_table(
        "preset",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("tier", sa.String(length=8), nullable=True),
        sa.Column("is_builtin", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "setting",
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.String(length=2048), nullable=False),
        sa.Column("value_type", sa.String(length=16), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )

    op.create_table(
        "calibration",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=True),
        sa.Column("hardware_fingerprint", sa.String(length=256), nullable=False),
        sa.Column("tile_curve", sa.JSON(), nullable=True),
        sa.Column("precision_decision", sa.String(length=16), nullable=True),
        sa.Column("recommended_tier", sa.String(length=8), nullable=True),
        sa.Column("reason", sa.String(length=1024), nullable=True),
        sa.Column("valid", sa.Boolean(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["model_id"], ["model.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_calibration_model_id_hardware_fingerprint_valid",
        "calibration",
        ["model_id", "hardware_fingerprint", "valid"],
    )


def downgrade() -> None:
    # 逆依赖序删除；迁移期 foreign_keys=OFF（env.py），DROP 不触发级联
    op.drop_index("ix_calibration_model_id_hardware_fingerprint_valid", table_name="calibration")
    op.drop_table("calibration")
    op.drop_table("setting")
    op.drop_table("preset")
    op.drop_table("model")
    op.drop_index("ix_artifact_task_id", table_name="artifact")
    op.drop_table("artifact")
    op.drop_index("ix_task_status_created_at", table_name="task")
    op.drop_table("task")
