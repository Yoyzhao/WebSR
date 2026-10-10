"""能力快照组装：阶段 A → 缓存 → 阶段 B → 档位判定（tech-arch §6.2 / §6.6 第 2 步）。

这是**唯一**对外提供能力声明的入口：`/api/system/capabilities` 与诊断导出都取它，
避免"到处读全局变量"（跨环境设计文档 §9）。

编排（对应启动序列第 2 步，**非阻塞**）：

```
阶段 A 能力探测（毫秒级）
   ↓
缓存命中？（硬件指纹一致）
   ├─ 是 → 复用上次的 EP 验证结论（首屏不必重跑 profile）
   └─ 否 → 阶段 B EP 真实性验证（跑一次 profile，数节点归属）→ 写缓存
   ↓
档位判定（§6.2：实读可用显存 + 「已验证」后端，**不按设备型号**）
```

**保底语义**：阶段 C（自标定）**已落地**（T-805）→ 是否需要保守下界由
「当前硬件指纹下是否存在**匹配的标定记录**」决定，而**不是**靠"检测到了显卡"推断档位——
档位第 3 条要求后端**已验证**（§6.2）。
⚠️ 本模块的 `using_fallback` 是**快照口径**（按"通用记录"取数）：无通用标定时即为 true，
**不等于**每个任务都走保底档 —— 任务级以 `resolved.source`（`calibration`/`user`/`fallback`）为准。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

from . import backend_cache
from .device_probe import DeviceFacts, probe_device_facts
from .ep_verify import (
    GPU_PROVIDERS as _GPU_PROVIDERS,
)
from .ep_verify import (
    VerificationResult,
    deliberately_excluded,
    hardware_fingerprint,
    verify_candidates,
)
from .fallback import DEFAULT_ALIGN
from .runtime_profile import CalibrationView, RuntimeRequest, decide_profile
from .simulation import SimulationOverride

logger = logging.getLogger("websr.engine.capabilities")

#: 档位边界（§6.2 判定规则，**机制常量**，与"数值运行时求"不冲突）
_T3_MIN_VRAM_MB = 48 * 1024
_T2_MIN_VRAM_MB = 16 * 1024

_LABELS = {
    "T0": "T0 · 纯 CPU",
    "T1": "T1 · 消费级 8G",
    "T2": "T2 · 高端 16–24G",
    "T3": "T3 · 专业卡",
}

_lock = threading.Lock()
#: 按数据根索引的进程内快照：`{data_root_key: (capabilities, details, facts)}`。
#: 按根索引是必须的——改 `data_root` 后旧快照必须作废（否则会读到上一个根的结论）。
_snapshots: dict[str, tuple[dict, dict, DeviceFacts | None]] = {}
#: 最近一次构建快照的数据根（门控层不带参数取 facts 时用）
_last_key: str | None = None


def _root_key(data_root: Path | str) -> str:
    try:
        return str(Path(data_root).resolve())
    except Exception:
        return str(data_root)


# ---------------------------------------------------------------------------
# 档位判定
# ---------------------------------------------------------------------------

def _derive_tier_hardware(facts: DeviceFacts, adopted_providers: list[str]) -> tuple[str, str, str]:
    """**纯硬件**档位判定（不含模拟）。判定顺序**自上而下，首个命中即定档**。

    1. 可用显存 ≥ 48 GB → T3
    2. 可用显存 ≥ 16 GB → T2
    3. 存在**已验证**的 GPU 后端 → T1
    4. 其余 → T0

    约束（§6.2）：判据用**实读可用显存**（不是标称值）；第 3 条只在后端**已验证**时成立，
    `session.get_providers()` 不算证据。**禁止**按设备型号判定。
    """
    vram = facts.available_vram_mb
    gpu_verified = [p for p in adopted_providers if p in _GPU_PROVIDERS]

    if vram is not None and vram >= _T3_MIN_VRAM_MB:
        return "T3", _LABELS["T3"], f"实读可用显存 {vram / 1024:.1f} GB ≥ 48 GB"
    if vram is not None and vram >= _T2_MIN_VRAM_MB:
        return "T2", _LABELS["T2"], f"实读可用显存 {vram / 1024:.1f} GB ≥ 16 GB"
    if gpu_verified:
        ev = "、".join(gpu_verified)
        return "T1", _LABELS["T1"], f"存在已验证的 GPU 后端（{ev}）"

    # 兜底说明：把"为什么不是 T1"讲清楚，避免用户以为是软件问题
    if facts.nvidia and not gpu_verified:
        reason = ("检测到 NVIDIA GPU，但其后端未通过 EP 真实性验证——"
                  "档位判定要求后端已验证（静默回退的代价是「看似加速实则更慢」），故按 T0 保守处理")
    elif facts.intel_gpu:
        reason = ("检测到 Intel GPU，但 OpenVINO 原生路径尚未验证/标定，"
                  "暂按 T0 保守处理（§6.2 约束 4：实际档位由首次标定裁决）")
    elif facts.probes_failed:
        reason = f"硬件探测部分失败（{'; '.join(facts.probes_failed[:2])}），按最保守档处理"
    else:
        reason = "未检测到可用 GPU 后端（纯 CPU 路径）"
    return "T0", _LABELS["T0"], reason


def derive_tier(
    facts: DeviceFacts,
    adopted_providers: list[str],
    simulation: SimulationOverride | None = None,
) -> tuple[str, str, str]:
    """(`tier`, `tier_label`, `tier_reason`)。**真实判定 + 档位模拟叠加**（T-901）。

    模拟**只覆盖判定输入**，不伪造硬件事实（`facts` 原对象不被修改）：
    - `force_tier` 直接定档；
    - 仅 `force_vram_mb` 时，用"声明的可用显存"重新推导一次；
    - 生效时 `tier_reason` **必须**出现"档位模拟"字样（PRD §2.3 原则 4：界面据此提示）。
    """
    real_tier, real_label, real_reason = _derive_tier_hardware(facts, adopted_providers)

    if simulation is None or not simulation.active:
        return real_tier, real_label, real_reason

    if simulation.force_tier:
        tier = simulation.force_tier
        return tier, f"{tier} · 模拟档位", simulation.tier_reason(tier, real_tier, real_reason)

    # 只声明了显存：按模拟后的可用显存重新推导（已推不出更高档时如实说明）
    sim_facts = simulation.apply_to_facts(facts) or facts
    tier, _label, sim_reason = _derive_tier_hardware(sim_facts, adopted_providers)
    vram_gb = (simulation.force_vram_mb or 0) / 1024
    reason = (
        f"档位模拟：可用显存声明为 {vram_gb:.1f} GB → 推导档位 {tier}"
        f"（本机真实档位 {real_tier}）。模拟档位仅用于测试，不代表真实能力。模拟推导：{sim_reason}"
    )
    return tier, f"{tier} · 模拟档位", reason


# ---------------------------------------------------------------------------
# 事实摘要（契约 DeviceFacts 形状）
# ---------------------------------------------------------------------------

def summarize_device_facts(facts: DeviceFacts) -> dict:
    gpu = facts.primary_nvidia
    cpu = facts.cpu

    def _gb(mb: int | None) -> float | None:
        return round(mb / 1024, 1) if mb is not None else None

    return {
        "cpu": cpu.name,
        "gpu": gpu.name if gpu else "未检测到独显（或 nvidia-smi 不可用）",
        "driver": (gpu.driver_version if gpu else None) or "未知",
        "system_ram_gb": _gb(cpu.ram_total_mb),
        "available_vram_gb": _gb(gpu.vram_free_mb if gpu else None),
        "nominal_vram_gb": _gb(gpu.vram_total_mb if gpu else None),
        # ↓ F-06 要求展示"CPU 核数与指令集""实读可用内存"，前端 DeviceFacts 类型尚无这两个字段
        #   （5C 已冻；差异挂账 T-700）
        "cpu_cores": cpu.logical_cores,
        "available_ram_gb": _gb(cpu.ram_available_mb),
    }


# ---------------------------------------------------------------------------
# 探针模型选择
# ---------------------------------------------------------------------------

def pick_probe_model(models_dir: Path) -> Path | None:
    """选一个用于 EP 验证的探针模型。

    规则：优先**非 fp16** 的 `.onnx`（fp16 图在部分后端上不被支持，做探针会误伤），
    其中取**体积最小**的。刻意不硬编码文件名——验证只需一个能跑通的最小图。
    """
    try:
        cands = [p for p in Path(models_dir).rglob("*.onnx") if p.is_file()]
    except Exception:
        return None
    if not cands:
        return None
    fp32 = [p for p in cands if "fp16" not in p.name.lower()]
    return min(fp32 or cands, key=lambda p: p.stat().st_size)


# ---------------------------------------------------------------------------
# 快照组装
# ---------------------------------------------------------------------------

def build_snapshot(
    data_root: Path | str,
    *,
    probe_model: Path | str | None = None,
    models_dir: Path | str | None = None,
    refresh: bool = False,
    ort_module=None,
    probe_size: int = 256,
    measure_latency: bool = True,
    calibration_provider: Callable[[str | None], CalibrationView | None] | None = None,
    simulation: SimulationOverride | None = None,
) -> tuple[dict, dict]:
    """执行完整编排，返回 `(capabilities, details)`。

    `probe_model` 优先；未给则从 `models_dir` 里按 `pick_probe_model` 规则选。
    `details` 供诊断导出使用（完整 DeviceFacts / 失败项 / 被剔除后端 / 缓存路径）。
    **永不抛异常**——任何环节失败都降级为 T0 + 保底档并如实记录。

    `calibration_provider` 是阶段 C 标定结论的**取数回调**（可选，应用层注入）：
    因为标定记录的匹配需要**硬件指纹**，而指纹要等阶段 A 探测完才知道，
    所以这里传回调而不是值——引擎层保持不读库（纯度），应用层提供 `指纹 → 标定记录` 的映射。
    无有效标定时 `using_fallback` 为 true（§6.8）。⚠️ 快照按数据根缓存，
    标定落定后需以 `refresh=True` 重建（T-805 接线时在标定完成回调中触发）。

    `simulation`（T-901）**只覆盖判定输入**：档位推导与 `force_has_tensorrt` 入链。
    模拟生效时**不读写 EP 验证缓存**——否则模拟态产出的 verdict 会被写进真实指纹的缓存里，
    之后即使关掉模拟也会命中一份"带模拟成分"的结论（缓存污染）。
    """
    sim = simulation or SimulationOverride()
    facts = probe_device_facts()

    excluded = deliberately_excluded(facts)
    result: VerificationResult | None = None
    cache_state = "miss"

    # 缓存命中即跳过 profile（§6.6 硬约束 1）。模拟态下一律绕过缓存（见 docstring）。
    cached = None
    if not sim.active:
        cached = None if refresh else backend_cache.load_as_result(
            data_root, hardware_fingerprint(facts)
        )
    else:
        cache_state = "bypassed_simulation"
    if cached is not None:
        result = cached
        cache_state = "hit"
        logger.info("EP 验证命中缓存（指纹一致），跳过 profile 采样")
    else:
        chosen = Path(probe_model) if probe_model else (
            pick_probe_model(Path(models_dir)) if models_dir else None
        )
        if chosen is None or not chosen.is_file():
            result = VerificationResult(hardware_fingerprint=hardware_fingerprint(facts))
            result.exception_events.append("未找到可用于验证的 .onnx 探针模型，跳过阶段 B")
            # 模拟态下"缓存被绕过"是更该被看到的事实（skipped 会让它看起来像普通路径）
            if not sim.active:
                cache_state = "skipped"
        else:
            result = verify_candidates(
                facts,
                str(chosen),
                probe_size=probe_size,
                measure_latency=measure_latency,
                ort_module=ort_module,
                force_tensorrt=sim.tensorrt_or(False),
            )
            if sim.active:
                pass  # 模拟态**不落盘**（见 docstring：避免污染真实指纹的缓存）
            elif result.adopted:
                backend_cache.save(data_root, result)
                cache_state = "written"
            else:
                # 无任何后端通过验证 → 不写缓存（否则会把"验证失败"固化下来，
                # 而失败可能是暂时的：驱动刚更新、DLL 路径刚修好）
                logger.warning("候选链中无后端通过验证，不写缓存：%s", result.exception_events)
                cache_state = "not_saved"

    tier, tier_label, tier_reason = derive_tier(facts, result.adopted, sim)

    evidence = [v.to_evidence() for v in result.verdicts]
    evidence += [v.to_evidence() for v in excluded]

    # 标定结论（阶段 C）：交给应用层按硬件指纹去取；取不到或抛异常一律按未标定处理
    cal_view: CalibrationView | None = None
    if calibration_provider is not None:
        try:
            cal_view = calibration_provider(result.hardware_fingerprint)
        except Exception as exc:  # 标定取数失败不得影响能力面板
            logger.debug("标定结论取数失败（按未标定处理）: %s", exc)

    # 保底语义**不再硬编码**：直接问阶段 D 要一次"自动档决策"的结果（T-804）。
    # 这样能力面板显示的"当前后端 / 精度 / 是否保底"与任务真正拿到的决策同源——
    # 标定（阶段 C）落地后此处的 `using_fallback` 会自动变为 false，无需改本文件。
    baseline = decide_profile(
        facts=facts,
        adopted=result.adopted,
        fingerprint=result.hardware_fingerprint,
        calibration=cal_view,
        request=RuntimeRequest(auto=True),
        align=DEFAULT_ALIGN,
    )
    active_backend = baseline.backend or (result.adopted[0] if result.adopted else "unverified")
    capabilities = {
        "tier": tier,
        "tier_label": tier_label,
        "tier_reason": tier_reason,
        "device_facts": summarize_device_facts(facts),
        "verified_backends": [
            v.to_verified_backend(precision=baseline.precision) for v in result.verdicts if v.usable
        ],
        "ep_evidence": evidence,
        "using_fallback": baseline.using_fallback,
        "active_backend": active_backend,
        "active_precision": baseline.precision,
        # 档位模拟（T-901）：字段集与契约 v1.0 一致（`{enabled, force_tier}`），
        # 但取值由**设置真实计算**（此前是写死的占位）。`enabled` 表示"确实改变了判定输入"。
        "simulation": sim.to_block(),
    }
    details = {
        "_facts": facts,  # 内部复用项：get_snapshot 会摘走，不下发给调用方
        "device_facts": facts.to_dict(),
        "probes_failed": list(facts.probes_failed),
        "probes_skipped": list(facts.probes_skipped),
        "probe_ms": dict(facts.probe_ms),
        "hardware_fingerprint": result.hardware_fingerprint,
        "cache_state": cache_state,
        "cache_path": str(backend_cache.cache_path(data_root)),
        "adopted_backends": list(result.adopted),
        "backend_latency_ms": dict(result.backend_latency_ms),
        "exception_events": list(result.exception_events),
        "excluded_backends": [
            {"provider": v.provider, "reason": v.reason} for v in excluded
        ],
        "ort_available_providers": list(facts.ort_available_providers),
        # 诊断用：模拟态下"真实硬件事实"与"生效判定输入"的对照（T-901）
        "simulation": {
            "enabled": sim.enabled,
            "active": sim.active,
            "force_tier": sim.force_tier,
            "force_vram_mb": sim.force_vram_mb,
            "force_has_tensorrt": sim.force_has_tensorrt,
            "real_tier": _derive_tier_hardware(facts, result.adopted)[0],
            "note": "模拟只覆盖判定输入，不伪造硬件事实；device_facts 恒为真实探测值。",
        },
    }
    return capabilities, details


def get_snapshot(
    data_root: Path | str,
    *,
    probe_model: Path | str | None = None,
    models_dir: Path | str | None = None,
    refresh: bool = False,
    ort_module=None,
    probe_size: int = 256,
    measure_latency: bool = True,
    calibration_provider: Callable[[str | None], CalibrationView | None] | None = None,
    simulation: SimulationOverride | None = None,
) -> tuple[dict, dict]:
    """进程内缓存的快照（避免每次 `/api/system/capabilities` 都重跑探测与 profile）。

    ⚠️ 模拟态变化后**必须**由调用方 `reset_snapshot()` 作废缓存，否则会继续返回
    上一套档位（应用层由 `services/simulation.invalidate()` 统一负责）。
    """
    key = _root_key(data_root)
    with _lock:
        hit = _snapshots.get(key)
        if hit is not None and not refresh:
            return hit[0], hit[1]
    built_caps, built_details = build_snapshot(
        data_root,
        probe_model=probe_model,
        models_dir=models_dir,
        refresh=refresh,
        ort_module=ort_module,
        probe_size=probe_size,
        measure_latency=measure_latency,
        calibration_provider=calibration_provider,
        simulation=simulation,
    )
    facts = built_details.pop("_facts", None)  # 内部复用项，不下发给调用方
    global _last_key
    with _lock:
        _snapshots[key] = (built_caps, built_details, facts)
        _last_key = key
    return built_caps, built_details


def get_details(data_root: Path | str | None = None) -> dict | None:
    """取快照的 details（诊断导出用）。不给 data_root 时取最近一次构建的。"""
    with _lock:
        key = _root_key(data_root) if data_root is not None else _last_key
        hit = _snapshots.get(key) if key else None
        return hit[1] if hit else None


def get_facts(data_root: Path | str | None = None) -> DeviceFacts | None:
    """取快照的 `DeviceFacts`（门控层复用，避免重跑探测）。"""
    with _lock:
        key = _root_key(data_root) if data_root is not None else _last_key
        hit = _snapshots.get(key) if key else None
        return hit[2] if hit else None


def reset_snapshot(data_root: Path | str | None = None) -> None:
    """清空进程内快照（测试与「重新探测」入口用）。不给参数则清空全部。"""
    global _last_key
    with _lock:
        if data_root is None:
            _snapshots.clear()
            _last_key = None
        else:
            _snapshots.pop(_root_key(data_root), None)
