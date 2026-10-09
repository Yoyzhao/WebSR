"""WebSR 后端入口（FastAPI）。

⚠️ uvicorn 必须单 worker（ADR-005 / tech-arch §5.1）：SSE 进度推送依赖
进程内广播，多 worker 下订阅连接与推送可能落在不同进程，事件会随机丢失。
启动命令禁止添加 `--workers`（保持默认 1）。

启动（在 server/ 下，端口见 docs/tech/dev-info.md §4，被占用时禁止自动换端口）：
    uv run --active uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.events import router as events_router
from .api.files import router as files_router
from .api.models import router as models_router
from .api.settings import router as settings_router
from .api.system import router as system_router
from .api.tasks import router as tasks_router
from .core.config import get_settings
from .core.errors import register_error_handlers
from .core.lifecycle import run_startup_blocking, schedule_startup_nonblocking
from .core.logging import setup_logging
from .services.model_registry import sync_builtin_models
from .tasks.manager import manager as task_manager

logger = logging.getLogger("websr.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    logger.info("WebSR 后端启动（单 worker；数据根 %s）", settings.data_root)
    # tech-arch §6.6 启动序列：第 1 步阻塞（T-603；数据库不可用会抛错拒绝启动），
    # 第 2 步非阻塞（阶段 A/B/C 调度挂载点，实现属 T-803~T-805）
    run_startup_blocking(app)
    schedule_startup_nonblocking(app)
    # M3 内置模型登记（T-604）：幂等，文件缺失只告警；依赖启动序列已建库
    sync_builtin_models()
    # M1 任务工作线程（T-606，ADR-005：并发度 1，不占事件循环）
    task_manager.start()
    yield
    task_manager.stop()


app = FastAPI(title="WebSR API", version="0.1.0", lifespan=lifespan)
register_error_handlers(app)
app.include_router(models_router)
app.include_router(files_router)
app.include_router(tasks_router)
app.include_router(events_router)
app.include_router(system_router)
app.include_router(settings_router)

# CORS：白名单（APP_CORS_ORIGINS），禁止 "*"（project-rules §2.3）；
# 开发期主要由 Vite proxy 规避跨域（dev-info §4），此处是直连场景的兜底
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict:
    """骨架级探活端点（T-601 最小启动验证用；契约身份待 T-700 定稿时确认）。"""
    return {"status": "ok", "service": "websr-server", "version": app.version}
