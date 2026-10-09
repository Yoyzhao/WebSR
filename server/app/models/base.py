"""ORM 基座：约束命名约定 + UTCDateTime 类型（ADR-006 §C-10）。

命名约定：Alembic batch 模式重建表时需要按名引用约束，缺省会生成不可控的名字。
"""
from datetime import datetime, timezone

from sqlalchemy import DateTime, MetaData, TypeDecorator
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UTCDateTime(TypeDecorator):
    """UTC 时间类型：写入要求 aware datetime（naive **直接抛错**，把错误挡在写入侧）；
    落库为 naive UTC 文本；读出统一贴 `timezone.utc`。

    为什么不用 `DateTime(timezone=True)`：SQLite 上两者落库文本完全相同，
    但后者读回会**丢失 tzinfo**（ADR-006 §C-10 实测），造成"假时区"。
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, _dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("UTCDateTime 只接受 aware datetime（naive 拒绝写入）")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, _dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)
