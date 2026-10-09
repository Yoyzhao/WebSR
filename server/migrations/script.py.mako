"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

⚠️ 审阅清单（ADR-006）：
- 使用自定义类型（UTCDateTime）时，autogenerate 不会补 import，须手工添加（约束 5）；
- 不得修改已在共享/生产环境应用的迁移；结构演进走新增迁移。
"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
