"""应用配置：统一读取 APP_* 环境变量（键名见 docs/tech/dev-info.md §6）。

关键约定（实现决策，勿随意改动）：
- `.env.dev` 位于**项目根**，不在 server/ 下；
- `APP_DATA_DIR` 与 `APP_DB_URL` 中的相对路径一律相对**项目根**解析 ——
  后端进程的工作目录是 server/，若按 cwd 解析会错写到 server/data/ 下
  （目录约定见 dev-info.md §3 与 project-rules.md §2.1）。
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# server/app/core/config.py → parents: [0]=core [1]=app [2]=server [3]=项目根
PROJECT_ROOT = Path(__file__).resolve().parents[3]

_SQLITE_PREFIX = "sqlite:///"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file=PROJECT_ROOT / ".env.dev",
        env_file_encoding="utf-8",
        extra="ignore",  # .env.dev 中的 HF_ENDPOINT 等非 APP_ 键忽略
    )

    data_dir: str = "./data"
    db_url: str = "sqlite:///./data/app.db"
    host: str = "127.0.0.1"  # 本地单机形态禁止 0.0.0.0（project-rules §2.3）
    port: int = 8000
    log_level: str = "info"
    max_upload_mb: int = 50
    cors_origins: str = "http://127.0.0.1:5173"  # 白名单，禁止 *（project-rules §2.3）

    @property
    def data_root(self) -> Path:
        """应用数据根（绝对路径）：models / uploads / outputs / thumbs / calibration / app.db。"""
        p = Path(self.data_dir)
        return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()

    @property
    def database_url(self) -> str:
        """SQLite 相对路径归一到项目根；Windows 反斜杠转正斜杠（SQLAlchemy URL 要求）。"""
        if self.db_url.startswith(_SQLITE_PREFIX):
            p = Path(self.db_url[len(_SQLITE_PREFIX):])
            if not p.is_absolute():
                p = (PROJECT_ROOT / p).resolve()
            return _SQLITE_PREFIX + p.as_posix()
        return self.db_url

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
