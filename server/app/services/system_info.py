"""系统信息与诊断组装（PRD §3.4 / api-contract §4.4）。

数据来源**全部**来自引擎层（阶段 A 能力探测 + 阶段 B EP 真实性验证）：

- `engine/device_probe.py` —— 纯事实（`DeviceFacts`），永不抛异常；
- `engine/ep_verify.py` —— EP 验证结论（判据：目标 EP 节点数 > 0 且 CPU 节点数 = 0）；
- `engine/backend_cache.py` —— 验证结果缓存（`data/calibration/`，硬件指纹失效）；
- `engine/capabilities.py` —— 编排 + 档位判定（§6.2）。

本模块只做**取数 + 组装成契约形状**，不含任何探测逻辑——
这样"探测来源"换实现时（例如将来加 Intel 路径）本模块无需改动。

**保底语义**：阶段 C（自标定）**已落地**（T-805）。本模块的 `using_fallback` 是
**快照口径**（按"通用记录"取数）—— 无通用标定记录时为 true，`active_precision` 随之为
保底档取值（§6.8）；**任务级**口径以 `resolved.source` 为准。诊断 JSON 满足"一键导出即可
解释为什么他慢/崩"（跨环境设计文档 §9）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from ..db import get_session
from ..engine import capabilities as caps_engine
from ..engine.runtime_profile import CalibrationView
from ..models.builtin_catalog import BUILTIN_MODELS
from ..models.entities import Calibration, Model, Task
from . import settings_store

logger = logging.getLogger("websr.services.system_info")

APP_VERSION = "0.1.0"


def probe_model_path() -> Path | None:
    """EP 验证的探针模型：内置模型中第一个 `.onnx`（fp32 主路径）。

    刻意用**内置模型**而不是"目录里随便一个 onnx"——验证必须在产品真正会加载的
    模型上进行，否则验证结论与实际使用脱节。文件缺失时返回 None（引擎会退化为
    目录扫描，再没有就跳过阶段 B，不阻断启动）。
    """
    root = settings_store.effective_model_dir()
    for spec in BUILTIN_MODELS:
        if spec.format == "onnx":
            p = root / spec.path
            if p.is_file():
                return p
    return None


def read_calibration_view(
    fingerprint: str | None, model_id: int | None = None
) -> CalibrationView | None:
    """按硬件指纹取标定结论（阶段 C 的消费侧，**T-804 只读不写**）。

    匹配规则：先精确匹配 `model_id`，再退到"通用记录"（`model_id is NULL`）。
    **指纹不符的完全不看**——失效判据是硬件指纹，不是时间（与 EP 缓存同一纪律）。
    取不到匹配记录时返回 None → 决策层**退回保底档保守下界**（不猜测，见 `decide_profile`）。
    """
    if not fingerprint:
        return None
    try:
        s = get_session()
        try:
            rows = s.scalars(
                select(Calibration)
                .where(Calibration.valid.is_(True))
                .order_by(Calibration.created_at.desc())
            ).all()
        finally:
            s.close()
    except Exception as exc:  # 读不到标定不是故障：回落保底档即可
        logger.debug("标定记录读取失败，按未标定处理: %s", exc)
        return None

    row = next(
        (c for c in rows if c.hardware_fingerprint == fingerprint and c.model_id == model_id),
        None,
    ) or next(
        (c for c in rows if c.hardware_fingerprint == fingerprint and c.model_id is None),
        None,
    )
    if row is None:
        return None

    # ↓ 消费口径：曲线里**显式给出的**推荐块尺寸（T-805 负责产出该字段）。
    #   不做任何猜测——取不到就留空，让决策退回保底下界（保守方向）。
    curve = row.tile_curve if isinstance(row.tile_curve, dict) else {}
    return CalibrationView(
        hardware_fingerprint=row.hardware_fingerprint,
        tile=curve.get("recommended_tile"),
        precision=row.precision_decision,
        backend=curve.get("recommended_backend"),
        concurrency=curve.get("concurrency"),
        reason=row.reason,
        valid=bool(row.valid),
    )


def _snapshot() -> tuple[dict, dict]:
    """取能力快照（进程内缓存；首次调用会跑一次阶段 A/B，之后为读缓存）。"""
    return caps_engine.get_snapshot(
        settings_store.effective_data_root(),
        probe_model=probe_model_path(),
        models_dir=settings_store.effective_model_dir(),
        # 引擎层不读库：把"指纹 → 标定结论"的取数交给应用层
        calibration_provider=read_calibration_view,
    )


def get_capability_snapshot() -> tuple[dict, dict]:
    """公开入口：应用层其他模块（阶段 D 决策，T-804）取同一份能力快照。

    走同一条缓存路径是**必须**的——否则会出现"启动时用内置模型做探针验证过、
    任务期又用目录扫描重验一次"的两套结论。
    """
    return _snapshot()


def build_capabilities() -> dict:
    """契约 `Capabilities` 形状（api-contract §4.4 / 前端 `Capabilities` 类型）。"""
    caps, _ = _snapshot()
    return caps


def warmup_capabilities() -> dict:
    """启动第 2 步（非阻塞）调用：跑一次阶段 A/B 并填充进程内缓存。

    返回一个**精简摘要**（写进 `app.state.capability_report` 供日志与诊断），
    而不是完整 capabilities——启动日志不需要把整个证据链打一遍。
    """
    caps, details = _snapshot()
    return {
        "tier": caps["tier"],
        "tier_reason": caps["tier_reason"],
        "adopted_backends": details.get("adopted_backends"),
        "cache_state": details.get("cache_state"),
        "hardware_fingerprint": details.get("hardware_fingerprint"),
        "probes_failed": details.get("probes_failed"),
        "probes_skipped": details.get("probes_skipped"),
        "exception_events": details.get("exception_events"),
    }


def build_diagnostics() -> dict:
    """一键诊断导出（PRD §3.4）：硬件事实 + 探测失败项 + EP 验证结果 + 标定 + 规模统计。"""
    caps, details = _snapshot()

    s = get_session()
    try:
        calibrations = s.scalars(select(Calibration).order_by(Calibration.created_at.desc())).all()
        cal_records = [{
            "id": c.id, "model_id": c.model_id,
            "hardware_fingerprint": c.hardware_fingerprint,
            "tile_curve": c.tile_curve, "precision_decision": c.precision_decision,
            "recommended_tier": c.recommended_tier, "reason": c.reason,
            "valid": c.valid, "created_at": c.created_at.isoformat() if c.created_at else None,
        } for c in calibrations]
        task_counts = dict(
            s.execute(select(Task.status, func.count()).group_by(Task.status)).all()
        )
        model_count = s.scalar(select(func.count()).select_from(Model))
    finally:
        s.close()

    try:
        cal_state = settings_store.effective("calibration_state")
    except Exception:
        cal_state = "pending"

    # 阶段 D/E（T-804）：最近一次决策 + 水位历史 + 降档链策略。
    # 局部 import 是必要的——`engine_decision` 依赖本模块取能力快照，
    # 模块级互相 import 会成环；诊断导出本身也**绝不能**因为它的异常而失败。
    try:
        from . import engine_decision

        decision = {
            "last": engine_decision.last_decision(),
            "policy": engine_decision.decision_policy(),
        }
        watermark_info = engine_decision.watermark_snapshot()
    except Exception as exc:
        decision = {"error": f"{type(exc).__name__}: {exc}"}
        watermark_info = {"error": f"{type(exc).__name__}: {exc}"}

    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "app_version": APP_VERSION,
        "tier": caps["tier"],
        "tier_label": caps["tier_label"],
        "tier_reason": caps["tier_reason"],
        # 契约字段（前端已消费）
        "device_facts": caps["device_facts"],
        "ep_evidence": caps["ep_evidence"],
        # ↓ 诊断专用：探测细节与失败项（"一键导出即可解释为什么他慢/崩"）
        "probe": {
            "device_facts_raw": details.get("device_facts"),
            "probes_failed": details.get("probes_failed"),
            "probes_skipped": details.get("probes_skipped"),
            "probe_ms": details.get("probe_ms"),
            "ort_available_providers": details.get("ort_available_providers"),
        },
        "ep_verification": {
            "hardware_fingerprint": details.get("hardware_fingerprint"),
            "cache_state": details.get("cache_state"),
            "cache_path": details.get("cache_path"),
            "adopted_backends": details.get("adopted_backends"),
            "backend_latency_ms": details.get("backend_latency_ms"),
            "excluded_backends": details.get("excluded_backends"),
            "exception_events": details.get("exception_events"),
        },
        "calibration": {
            "state": cal_state,
            "records": cal_records,
            "note": "首启自标定（阶段 C / T-805）已实现：按「硬件指纹 + 模型」匹配，命中即用实测参数，"
                    "未命中退回保底档保守下界；此处仅导出记录，任务级来源见 resolved.source",
        },
        # ↓ 阶段 D/E（T-804）：决策产物 + 水位反馈
        "decision": decision,
        "watermark": watermark_info,
        "summary": {
            "model_count": model_count,
            "task_count_by_status": task_counts,
        },
        "using_fallback": caps["using_fallback"],
        "active_backend": caps["active_backend"],
        "active_precision": caps["active_precision"],
    }
