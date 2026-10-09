"""EP 验证结果缓存（tech-arch §6.6 硬约束 1 / ADR-004 §实施约束）。

> EP 验证结果与标定结果**必须落缓存**（`data/calibration/`），
> 否则每次启动都要重跑 profile，首屏体验不可接受。

**失效判据是硬件指纹**，不是时间：驱动升级、换卡、ORT 版本变化都会改变验证结论，
指纹不符即整份作废（宁可重跑一次 profile，也不用过期结论）。

`simulated: true` 的结果**不得写入**本缓存（tech-arch §6.3 约束 2，
例如档位模拟产生的验证结论不能污染真实缓存）。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from .ep_verify import VerificationResult

logger = logging.getLogger("websr.engine.backend_cache")

#: 相对 `<data_root>/calibration/` 的文件名
CACHE_FILENAME = "verified_backends.json"


def cache_dir(data_root: Path | str) -> Path:
    d = Path(data_root) / "calibration"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_path(data_root: Path | str) -> Path:
    return cache_dir(data_root) / CACHE_FILENAME


def serialize(result: VerificationResult, *, simulated: bool = False) -> dict:
    return {
        "hardware_fingerprint": result.hardware_fingerprint,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "simulated": simulated,
        "adopted": list(result.adopted),
        "backend_latency_ms": dict(result.backend_latency_ms),
        "exception_events": list(result.exception_events),
        "verdicts": [
            {
                "provider": v.provider,
                "usable": v.usable,
                "reason_code": v.reason_code,
                "reason": v.reason,
                "node_assignment": v.node_assignment,
                "node_count": v.node_count,
                "cpu_node_count": v.cpu_node_count,
                "latency_ms": v.latency_ms,
                "adopted": v.adopted,
                "note": v.note,
            }
            for v in result.verdicts
        ],
    }


def save(data_root: Path | str, result: VerificationResult, *, simulated: bool = False) -> Path | None:
    """写入缓存。`simulated=True` 时**拒绝写入**（§6.3 约束 2）。"""
    if simulated:
        logger.info("档位模拟结果不写入正式缓存（tech-arch §6.3），跳过持久化")
        return None
    p = cache_path(data_root)
    payload = serialize(result, simulated=False)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)  # 原子替换：避免半截 JSON 被下次启动读到
    logger.info("EP 验证结果已缓存：%s（采用 %s）", p, result.adopted or "无")
    return p


def load(data_root: Path | str, fingerprint: str) -> dict | None:
    """读取缓存。**指纹不符或损坏 → None**（调用方据此重跑验证）。"""
    p = cache_path(data_root)
    if not p.is_file():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("EP 验证缓存损坏，将重跑验证：%s", exc)
        return None
    if payload.get("hardware_fingerprint") != fingerprint:
        logger.info("硬件指纹已变化，EP 验证缓存作废（将重跑验证）")
        return None
    if payload.get("simulated"):
        # 理论上不会出现（save 已拒写）；真出现说明被外部篡改，按不可信处理
        logger.warning("缓存标记为模拟结果，按不可信处理，将重跑验证")
        return None
    return payload


def load_as_result(data_root: Path | str, fingerprint: str) -> VerificationResult | None:
    """缓存 → `VerificationResult`（供能力组装直接复用，不重跑 profile）。"""
    payload = load(data_root, fingerprint)
    if payload is None:
        return None
    from .ep_verify import BackendVerdict

    result = VerificationResult(hardware_fingerprint=payload["hardware_fingerprint"])
    result.adopted = list(payload.get("adopted") or [])
    result.backend_latency_ms = dict(payload.get("backend_latency_ms") or {})
    result.exception_events = list(payload.get("exception_events") or [])
    result.verdicts = [BackendVerdict(**v) for v in payload.get("verdicts") or []]
    return result
