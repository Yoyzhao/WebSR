"""后端启动序列（tech-arch §6.6）：**只有第 1 步阻塞**。

第 1 步（阻塞，本模块 `run_startup_blocking`）：
    建库/迁移 → 注册推理运行时 DLL 路径 → 回收 `running`/`canceling` → `interrupted`
    - 数据库不可用 → **拒绝启动**（抛异常让 uvicorn 退出并明确报错）；
    - DLL 注册失败 → 降级为仅 CPU 可用，**不阻断启动**（记 warning）；
    - 迁移必须排在状态回收**之前**（ADR-006 约束 7：否则表尚不存在）。

第 2 步（非阻塞，`schedule_startup_nonblocking`）：
    阶段 A 能力探测 → 阶段 B EP 真实性验证 → 无有效标定时投递后台标定（阶段 C）。
    实现属 T-803~T-805，此处只挂**调度框架**：任何失败只记日志（异常事件），
    任务按保底档继续（tech-arch §6.8）。
"""
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import text

from ..db import get_session
from ..engine.runtime_env import prepare_dll_paths
from ..services import settings_store
from ..services.settings_store import cleanup_expired_tasks

logger = logging.getLogger("websr.lifecycle")

# server/app/core/lifecycle.py → parents: [0]=core [1]=app [2]=server
SERVER_DIR = Path(__file__).resolve().parents[2]


def _run_migrations() -> None:
    """以编程方式执行 `alembic upgrade head`（URL 由 env.py 经 APP_DB_URL 注入）。

    不走 CLI 子进程：避免依赖 alembic 对 pyproject 的 CLI 探测行为，
    且迁移连接与运行时复用同一 make_engine()（ADR-006 约束 9）。
    """
    cfg = Config()
    cfg.set_main_option("script_location", str(SERVER_DIR / "migrations"))
    command.upgrade(cfg, "head")


def _recover_interrupted_tasks() -> int:
    """把上次遗留的 `running` / `canceling` 回收为 `interrupted`（ADR-005 实施约束 2）。

    幂等：再次执行更新 0 行。`finished_at` 一并落库，供界面展示中断时点。
    """
    with get_session() as s:
        result = s.execute(
            text(
                "UPDATE task SET status='interrupted', finished_at=:now "
                "WHERE status IN ('running', 'canceling')"
            ),
            {"now": datetime.now(timezone.utc)},
        )
        s.commit()
        return result.rowcount or 0


def run_startup_blocking(app: FastAPI) -> None:
    """启动序列第 1 步（阻塞）。结果写入 `app.state.startup_report`（供诊断导出 T-608）。"""
    report: dict = {}

    try:
        _run_migrations()
    except Exception as exc:
        # tech-arch §6.6：数据库不可用 → 拒绝启动并明确报错
        raise RuntimeError(f"数据库初始化/迁移失败，拒绝启动：{type(exc).__name__}: {exc}") from exc
    report["migrations"] = "ok"
    logger.info("启动序列 1/4：数据库迁移完成（upgrade head）")

    # 库已就绪 → 按落库配置重设日志级别（F-07：log_level 保存后重启仍生效）。
    # 放在迁移之后是必须的：迁移前读 SETTING 表会失败，只能退回 env 值。
    try:
        settings_store.apply_log_level(settings_store.effective_str("log_level"))
    except Exception as exc:  # 配置读取异常不得阻断启动
        logger.warning("日志级别按落库配置重设失败，沿用 env 值：%s", exc)

    dll = prepare_dll_paths()
    report["dll"] = dll
    if dll["failed"]:
        # tech-arch §6.6：DLL 注册失败 → 降级为仅 CPU 可用，不阻断启动
        logger.warning(
            "启动序列 2/4：推理运行时 DLL 路径注册部分失败（%d 项），相关后端将不可用、"
            "降级为仅 CPU：%s", len(dll["failed"]), dll["failed"],
        )
    else:
        logger.info("启动序列 2/4：DLL 路径注册完成（%d 个候选目录）", len(dll["added"]))

    recovered = _recover_interrupted_tasks()
    report["recovered_interrupted"] = recovered
    if recovered:
        logger.warning("启动序列 3/4：回收上次遗留的 %d 个运行中任务为 interrupted", recovered)
    else:
        logger.info("启动序列 3/4：无遗留运行中任务")

    # 保留策略清理（F-07）：只删超期**终态任务记录**，磁盘产出文件不动（见 settings_store）
    try:
        cleaned = cleanup_expired_tasks()
        report["retention_cleaned"] = cleaned
        if cleaned:
            logger.info("启动序列 4/4：按保留策略清理了 %d 条超期任务记录（产出文件保留）", cleaned)
        else:
            logger.info("启动序列 4/4：无超期任务记录需清理")
    except Exception as exc:
        # 清理失败不影响服务可用性（任务记录多留几天无副作用）
        report["retention_cleaned"] = 0
        logger.warning("启动序列 4/4：保留策略清理失败（不影响启动）：%s", exc)

    app.state.startup_report = report


def schedule_startup_nonblocking(app: FastAPI) -> None:
    """启动序列第 2 步（非阻塞）：阶段 A 能力探测 → 阶段 B EP 真实性验证。

    放线程里跑（`to_thread`）：EP 验证要跑一次 profile 采样，属**秒级**操作，
    放在事件循环里会卡住第一批请求——而这一步按 design 本就**不阻塞**。
    **任何失败只记异常事件**，任务按保底档继续执行（tech-arch §6.8）：
    探测失败 ≠ 服务不可用，只是拿不到加速。
    阶段 C（自标定）属 S2（T-805），到位后在此追加投递。
    """

    async def _capability_bootstrap() -> None:
        try:
            from ..services import system_info  # 局部 import：避免启动早期循环依赖

            report = await asyncio.to_thread(system_info.warmup_capabilities)
            app.state.capability_report = report
            logger.info(
                "启动第 2 步（非阻塞）：能力探测 / EP 验证完成 → 档位 %s，采用 %s，缓存 %s%s",
                report.get("tier"),
                report.get("adopted_backends") or "无（将用保底档）",
                report.get("cache_state"),
                f"，探测失败项：{report['probes_failed']}" if report.get("probes_failed") else "",
            )
        except Exception:
            # 第 2 步任何失败只记异常事件，不影响服务可用性（保底档兜底，§6.8）
            logger.exception("启动第 2 步（能力探测 / EP 验证）失败，将按保底档运行")

    app.state.capability_task = asyncio.create_task(_capability_bootstrap())
