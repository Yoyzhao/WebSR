"""Alembic 环境（ADR-006 约束落点，勿随意改动）：

- 约束 1：`render_as_batch=True` + `compare_type=True`（SQLite 不支持 ALTER COLUMN）；
- 约束 2：迁移期 `foreign_keys="OFF"` —— batch 重建表 DROP TABLE 时若 FK=ON，
  `ON DELETE CASCADE` 会**静默清空子表**（附录 C 有对照实验原始证据）；
- 约束 3：PRAGMA 全部由 `make_engine()` 的 connect 事件注入，本文件**不得**
  在 `context.configure()` 前用 `exec_driver_sql` 执行任何 SQL；
- 约束 6：DB URL 不硬编码，经 `get_settings()` 读 `APP_DB_URL`（.env.dev）；
- 约束 9：迁移与运行时复用同一 `make_engine()` 工厂。
"""
import sys
from pathlib import Path

from alembic import context

# server/ 上 sys.path，使 app 包可导入（约束 5：自定义类型模块须可导入）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import make_engine  # noqa: E402
from app.models import entities  # noqa: E402, F401  # 导入即注册全部表到 metadata
from app.models.base import Base  # noqa: E402

target_metadata = Base.metadata


def run_migrations_online() -> None:
    connectable = make_engine(foreign_keys="OFF")  # 约束 2
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # 约束 1
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
