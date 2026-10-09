"""引擎与会话工厂 —— PRAGMA 注入的**唯一落点**（ADR-006 约束 3/8/9）。

要点（均有实测证据，勿随意改动）：
- 运行时与 Alembic 迁移**共用** `make_engine()`，保证 PRAGMA 强一致（约束 9）；
- PRAGMA 只能在 connect 事件里设置，禁止在 `context.configure()` 前
  `exec_driver_sql`（会隐式开事务导致 alembic_version 丢失，约束 3）；
- 迁移期必须 `foreign_keys="OFF"`（约束 2：batch 重建表 DROP TABLE 会触发
  `ON DELETE CASCADE` **静默清空子表**）；
- 显式 `busy_timeout`；**禁止** `connect_args={"timeout": 0}`（约束 8）。
"""
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .core.config import get_settings

# 运行时 PRAGMA 基线；foreign_keys 由 make_engine 参数覆盖（迁移期传 OFF）
PRAGMAS = {
    "journal_mode": "WAL",      # 库文件持久（写入文件头）
    "foreign_keys": "ON",       # 每连接；SQLite 默认 OFF，不设则静默忽略外键
    "busy_timeout": "5000",     # 显式声明，防被置 0
    "synchronous": "NORMAL",
}


def make_engine(url: str | None = None, *, foreign_keys: str = "ON") -> Engine:
    settings = get_settings()
    url = url or settings.database_url
    # SQLite 不会自建父目录；数据根由应用负责创建（dev-info §3 目录约定）
    settings.data_root.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url)
    pragmas = {**PRAGMAS, "foreign_keys": foreign_keys}

    @event.listens_for(engine, "connect")
    def _apply_pragmas(dbapi_conn, _record) -> None:
        cur = dbapi_conn.cursor()
        for key, value in pragmas.items():
            cur.execute(f"PRAGMA {key}={value}")
        cur.close()

    return engine


_engine: Engine | None = None
SessionLocal = sessionmaker(expire_on_commit=False)


def get_engine() -> Engine:
    """运行时引擎（懒加载单例；启动序列 T-603 接管其初始化时机）。"""
    global _engine
    if _engine is None:
        _engine = make_engine()
        SessionLocal.configure(bind=_engine)
    return _engine


def get_session() -> Session:
    get_engine()
    return SessionLocal()
