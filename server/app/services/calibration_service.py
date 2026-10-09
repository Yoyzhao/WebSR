"""阶段 C 的应用层：触发、落库、查询（F-10 / T-805）。

引擎层（`engine/calibration.py`）只回答"测出什么"；本模块回答"**谁来跑、存哪儿、
怎么被看到**"——与 `engine_decision` 对 `runtime_profile` 的分工同构。

## 三条边界

1. **标定作业不进 `TASK` 表**。它不是"用户图片的超分任务"，混进任务历史会干扰
   M1 的语义（任务历史 = 用户的图片处理记录）。标定是**系统级操作**，状态挂在
   本模块的进程内状态里，端点也在 `/api/system/*` 下。
2. **模拟结果不落库**。`simulation_enabled` 打开时产出的是 `simulated=true` 的示意结论，
   `is_storable()` 会拒绝它——否则会被阶段 D 当成真实结论继承（§6.8 规则 2 同源）。
3. **同一时刻只跑一个标定**。标定吃满 GPU，并发跑两个只会让两个都变慢、且互相污染
   显存基线。重复触发直接返回当前状态。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from ..db import get_session
from ..engine import calibration as calib_engine
from ..models.entities import Calibration, Model
from . import engine_decision, settings_store, system_info

logger = logging.getLogger("websr.services.calibration")

_LOCK = threading.Lock()
_STATE: dict = {
    "job_id": None,
    "status": "idle",  # idle | running | completed | failed | skipped
    "started_at": None,
    "finished_at": None,
    "progress": [],
    "error": None,
    "stored": False,
    "skipped_reason": None,
    "outcome": None,  # calib_engine.CalibrationOutcome | None
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public_state() -> dict:
    outcome = _STATE.get("outcome")
    return {
        "job_id": _STATE["job_id"],
        "status": _STATE["status"],
        "started_at": _STATE["started_at"],
        "finished_at": _STATE["finished_at"],
        "error": _STATE["error"],
        "stored": _STATE["stored"],
        "skipped_reason": _STATE["skipped_reason"],
        "progress": list(_STATE["progress"])[-20:],
        "outcome": outcome.to_dict() if outcome is not None else None,
    }


# ---------------------------------------------------------------------------
# 目标解析
# ---------------------------------------------------------------------------


def _resolve_target() -> tuple[Path, int | None] | None:
    """标定对象 = 内置探针模型（与阶段 B 用同一个），并回填它在 `MODEL` 表里的 id。

    用内置模型而不是"目录里随便一个"：标定结论会进 `CALIBRATION` 表并被阶段 D 消费，
    必须在**产品真正会加载的模型**上做（与 `probe_model_path` 同一条纪律）。
    """
    path = system_info.probe_model_path()
    if path is None or not path.is_file():
        return None
    model_id: int | None = None
    session = get_session()
    try:
        row = session.scalars(
            select(Model).where(Model.path == path.name).limit(1)
        ).first()
        if row is not None:
            model_id = row.id
    except Exception as exc:  # 查不到 id 不影响标定，只是记录退化为"通用"
        logger.debug("标定目标查询模型 id 失败（按通用记录处理）：%s", exc)
    finally:
        session.close()
    return path, model_id


def _context() -> dict:
    """当前硬件与后端上下文。任何一步取不到都不阻断标定（用保守默认）。"""
    try:
        caps, details = system_info.get_capability_snapshot()
    except Exception as exc:
        logger.warning("标定取能力快照失败，按未验证环境处理：%s", exc)
        caps, details = {}, {}
    adopted = list(details.get("adopted_backends") or [])
    return {
        "fingerprint": details.get("hardware_fingerprint") or "unknown",
        "tier": caps.get("tier") or "T0",
        "adopted": adopted,
        "backend": adopted[0] if adopted else "CPUExecutionProvider",
    }


# ---------------------------------------------------------------------------
# 触发
# ---------------------------------------------------------------------------


def trigger(*, reason: str = "manual") -> dict:
    """触发一轮标定（**异步**，立即返回）。已有作业在跑时不重复触发。"""
    with _LOCK:
        if _STATE["status"] == "running":
            return {"started": False, "reason": "已有标定在进行中", **_public_state()}

        job_id = f"calib_{int(time.time())}"
        _STATE.update(
            job_id=job_id,
            status="running",
            started_at=_now_iso(),
            finished_at=None,
            progress=[],
            error=None,
            stored=False,
            skipped_reason=None,
            outcome=None,
        )
        threading.Thread(target=_run, args=(job_id, reason), name="calibration", daemon=True).start()
        return {"started": True, "task_id": job_id, "status": "running"}


def ensure_startup_calibration() -> dict | None:
    """启动序列调用：**已有有效标定就什么都不做**，否则投递后台标定（非阻塞）。

    返回 None 表示"无需标定"；返回 dict 是 `trigger()` 的返回值。
    """
    try:
        caps, details = system_info.get_capability_snapshot()
        fingerprint = details.get("hardware_fingerprint")
    except Exception as exc:
        logger.warning("启动标定前置快照失败，跳过（不阻断启动）：%s", exc)
        return None

    target = _resolve_target()
    if target is None:
        with _LOCK:
            _STATE.update(status="skipped", skipped_reason="模型库中没有可用的 .onnx 探针模型")
        logger.info("标定跳过：模型库中没有可用的 .onnx 探针模型")
        return None

    _path, model_id = target
    if fingerprint:
        existing = system_info.read_calibration_view(fingerprint, model_id)
        if existing is not None and existing.usable_with(fingerprint):
            logger.info("标定跳过：已有匹配当前硬件指纹的有效记录")
            return None

    logger.info("未找到匹配当前硬件的标定记录，投递后台标定")
    return trigger(reason="startup")


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------


def _run(job_id: str, reason: str) -> None:
    try:
        target = _resolve_target()
        if target is None:
            with _LOCK:
                _STATE.update(
                    status="skipped", finished_at=_now_iso(),
                    skipped_reason="模型库中没有可用的 .onnx 探针模型",
                )
            return
        model_path, model_id = target

        ctx = _context()
        simulate = settings_store.effective_bool("simulation_enabled")

        # 模型输入约束：静态模型只有一个合法块尺寸（只收紧不放宽，见 T-806）
        model_row = _model_entity(model_id)
        align, fixed_tile, constraint_note = engine_decision.model_constraints(model_row)

        def _report(stage: str, message: str) -> None:
            with _LOCK:
                _STATE["progress"].append(f"{stage}: {message}")

        logger.info(
            "开始标定（%s）：模型=%s 后端=%s 档位=%s align=%s fixed_tile=%s 模拟=%s",
            reason, model_path.name, ctx["backend"], ctx["tier"], align, fixed_tile, simulate,
        )

        outcome = calib_engine.run_calibration(
            model_path=model_path,
            fingerprint=ctx["fingerprint"],
            tier=ctx["tier"],
            model_id=model_id,
            backend=ctx["backend"],
            align=align,
            fixed_tile=fixed_tile,
            simulate=simulate,
            report=_report,
        )
        if constraint_note:
            logger.info("模型约束：%s", constraint_note)

        stored = False
        if outcome.is_storable():
            stored = _store(outcome)
        else:
            logger.info(
                "标定结论不入库（simulated=%s cancelled=%s tile=%s）",
                outcome.simulated, outcome.cancelled, outcome.recommended_tile,
            )

        with _LOCK:
            _STATE.update(
                status="completed" if outcome.is_storable() else "skipped",
                finished_at=_now_iso(),
                stored=stored,
                outcome=outcome,
                skipped_reason=None if outcome.is_storable() else outcome.reason[:200],
            )
        logger.info(
            "标定完成：推荐块尺寸=%s 精度=%s 耗时=%.1fs 入库=%s",
            outcome.recommended_tile, outcome.precision_decision,
            outcome.elapsed_ms / 1000.0, stored,
        )
    except Exception as exc:  # 标定失败只影响"自动档是否升级"，绝不外溢
        logger.warning("标定失败（保持保底档，不影响任务执行）：%s", exc, exc_info=True)
        with _LOCK:
            _STATE.update(status="failed", finished_at=_now_iso(), error=f"{type(exc).__name__}: {exc}")


def _model_entity(model_id: int | None):
    if model_id is None:
        return None
    session = get_session()
    try:
        return session.get(Model, model_id)
    except Exception:
        return None
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 落库 / 查询
# ---------------------------------------------------------------------------


def _store(outcome: calib_engine.CalibrationOutcome) -> bool:
    """写 `CALIBRATION` 行，并把同 (模型 × 指纹) 的旧记录置为失效。"""
    session = get_session()
    try:
        old = session.scalars(
            select(Calibration).where(
                Calibration.hardware_fingerprint == outcome.fingerprint,
                Calibration.valid.is_(True),
            )
        ).all()
        for row in old:
            # 只有"同一个模型 + 同一指纹"的旧结论作废；别的模型的标定不动
            if row.model_id == outcome.model_id:
                row.valid = False

        session.add(
            Calibration(
                model_id=outcome.model_id,
                hardware_fingerprint=outcome.fingerprint,
                tile_curve=outcome.to_curve(),
                precision_decision=outcome.precision_decision,
                recommended_tier=outcome.recommended_tier or outcome.tier,
                reason=(outcome.reason or "")[:1024],
                valid=True,
            )
        )
        session.commit()
        return True
    except Exception as exc:
        session.rollback()
        logger.warning("标定结论落库失败（不影响本次任务执行）：%s", exc)
        return False
    finally:
        session.close()


def latest_records(limit: int = 10) -> list[dict]:
    """最近的标定记录（倒序），供 `GET /api/system/calibration` 与诊断使用。"""
    session = get_session()
    try:
        rows = session.scalars(
            select(Calibration).order_by(Calibration.created_at.desc()).limit(limit)
        ).all()
        return [
            {
                "id": r.id,
                "model_id": r.model_id,
                "hardware_fingerprint": r.hardware_fingerprint,
                "tile_curve": r.tile_curve,
                "precision_decision": r.precision_decision,
                "recommended_tier": r.recommended_tier,
                "reason": r.reason,
                "valid": bool(r.valid),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    except Exception as exc:
        logger.warning("标定记录查询失败：%s", exc)
        return []
    finally:
        session.close()


def describe() -> dict:
    """`GET /api/system/calibration` 的响应体（api-contract §4.4 草案扩展）。"""
    records = latest_records()
    valid = [r for r in records if r["valid"]]
    current = valid[0] if valid else None
    tier = current["recommended_tier"] if current else None
    reasons: list[str] = []
    if current:
        curve = current["tile_curve"] if isinstance(current["tile_curve"], dict) else {}
        reasons.append(curve.get("reason") or current.get("reason") or "")
    else:
        reasons.append("尚无有效标定记录：'自动'档按保底档运行")

    return {
        "records": records,
        "recommended_tier": tier,
        "reasons": [r for r in reasons if r],
        "state": _public_state(),
    }


def reset_state() -> None:
    """测试用：清空进程内标定状态。"""
    with _LOCK:
        _STATE.update(
            job_id=None, status="idle", started_at=None, finished_at=None,
            progress=[], error=None, stored=False, skipped_reason=None, outcome=None,
        )
