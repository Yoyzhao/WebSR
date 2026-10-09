"""阶段 B：EP 真实性验证（tech-arch §6.1 阶段 B；跨环境设计文档 §5）。

**为什么必须实测**：`session.get_providers()` 只证明 EP 对象被创建，**不证明它执行了计算**。
ORT 在 EP 创建失败（DLL 缺失、ABI 不匹配）时**只打一条 warning 就静默回退 CPU**——
性能差一个数量级却毫无提示。唯一可信的证据是 **ORT profiling JSON 的节点级归属**。

**判据（不可简化）**：目标 EP 节点数 > 0 **且** CPU 节点数 = 0。
`节点数 == 1` 是"整图融合成单个子图"的**正常现象**，不能据此判为未接管——
所以交叉判据必须是"CPU 节点数"，不是"节点数够不够多"。

本模块只依赖标准库 + 惰性 `onnxruntime`；不 import 应用层（保持引擎层纯净，可被
任意环境独立调用，例如用 `.venvs/sr-gpu` 复核真实 CUDA EP）。

`tools/p0_1_cuda_smoke.py` 是本模块的雏形，其判据被原样吸收为产品代码
（tech-arch §6.7）；产品代码**不得** import `tools/`。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field

from .device_probe import DeviceFacts

logger = logging.getLogger("websr.engine.ep_verify")

# ---------------------------------------------------------------------------
# 防御性常量（可硬编码的只有"因果性防御"，不是实测数值——project-rules §2.2）
# ---------------------------------------------------------------------------

#: ORT 默认的 `kNextPowerOfTwo` 在 fp32 + 大 tile 下实测劣化 10.3 倍（显存占用几乎相同）。
#: 这是**因果性防御**，与具体硬件无关，必须显式覆盖（P0 报告 §4）。
_ARENA_EXTEND_STRATEGY = "kSameAsRequested"

#: GPU 后端上 CPU 节点占比超过此值时视为"不划算"，从候选链剔除（设计文档 §5.2）。
#: 这是**判定规则（机制）**，不是性能参数。
_CPU_NODE_RATIO_LIMIT = 0.30

#: 验证用输入的默认边长（必须能被模型接受；模型为静态形状时以其自身维度为准）。
_DEFAULT_PROBE_SIZE = 256

#: ORT provider 名 → 契约 BackendId（前端 `BackendId` 类型）
_PROVIDER_TO_BACKEND_ID = {
    "CUDAExecutionProvider": "cuda",
    "TensorrtExecutionProvider": "tensorrt",
    "OpenVINOExecutionProvider": "openvino",
    "CPUExecutionProvider": "cpu",
}

_PROVIDER_LABELS = {
    "CUDAExecutionProvider": "CUDA",
    "TensorrtExecutionProvider": "TensorRT",
    "OpenVINOExecutionProvider": "OpenVINO EP",
    "CPUExecutionProvider": "CPU",
}

#: 视为"GPU 后端"的 provider 集合——**后端域的事实**，故定义在此处，
#: 由档位判定（`capabilities.derive_tier`）与阶段 D 决策（`runtime_profile`）共同引用，
#: 避免两处各写一份而漂移。TensorRT 属 G-04（需 T-902 能力声明才入候选链），
#: 但一旦出现在 adopted 列表里，它就是 GPU 后端。
GPU_PROVIDERS = frozenset({"CUDAExecutionProvider", "TensorrtExecutionProvider"})


def backend_id(provider: str) -> str:
    return _PROVIDER_TO_BACKEND_ID.get(provider, provider)


def provider_label(provider: str) -> str:
    return _PROVIDER_LABELS.get(provider, provider)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class BackendVerdict:
    """单个 EP 的验证结论（**verdict 而非 bool**——失败原因必须可解释）。"""

    provider: str
    usable: bool
    reason_code: str
    reason: str
    node_assignment: dict = field(default_factory=dict)
    node_count: int = 0
    cpu_node_count: int = 0
    latency_ms: float | None = None
    adopted: bool = False
    note: str = ""

    @property
    def backend_id(self) -> str:
        return backend_id(self.provider)

    def to_evidence(self) -> dict:
        """契约 `EpEvidence` 形状（api-contract §4.4）。"""
        if self.reason_code in ("ep_not_available", "ort_not_installed", "probe_model_missing"):
            # 根本没参与验证（该 wheel 不含此 EP / 没装 ORT / 没探针模型）——
            # 不能写成"0 节点（未接管计算）"，那会被误读成"跑过但没接管"
            ownership = f"未参与验证（{self.reason_code}）"
        elif self.provider == "CPUExecutionProvider":
            # CPU 自身就是"回落到 CPU"的目的地，node_count == cpu_node_count 是同一批节点
            ownership = f"{self.node_count} 节点全在 CPU"
        elif self.cpu_node_count > 0 and self.node_count > 0:
            ownership = f"{self.node_count} 节点在本后端，另有 {self.cpu_node_count} 节点回落到 CPU"
        elif self.node_count > 0:
            ownership = f"{self.node_count} 节点全在本后端（CPU 节点 0 个）"
        else:
            ownership = "0 节点（未接管计算）"
        return {
            "provider": self.provider,
            "node_ownership": ownership,
            "node_count": self.node_count,
            "cpu_node_count": self.cpu_node_count,
            "verified": self.adopted,
            "note": self.note or self.reason,
        }

    def to_verified_backend(self, *, precision: str) -> dict:
        """契约 `VerifiedBackend` 形状（api-contract §4.4）。"""
        return {
            "id": self.backend_id,
            "label": self.provider,
            "verified": self.adopted,
            "precision": precision,
            "node_count": self.node_count,
            "cpu_node_count": self.cpu_node_count,
        }


# ---------------------------------------------------------------------------
# profile 解析
# ---------------------------------------------------------------------------

def count_nodes_by_provider(profile_path: str) -> dict[str, int]:
    """统计 ORT profile 中**节点级事件**的 provider 归属。

    只统计 `cat == "Node"` 的事件——其余是 Session/Model/算子初始化等噪声事件。
    """
    with open(profile_path, encoding="utf-8") as fh:
        data = json.load(fh)
    events = data.get("traceEvents", []) if isinstance(data, dict) else data
    counts: dict[str, int] = {}
    for ev in events:
        if not isinstance(ev, dict) or ev.get("cat") != "Node":
            continue
        prov = (ev.get("args") or {}).get("provider", "?")
        counts[prov] = counts.get(prov, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# 候选链
# ---------------------------------------------------------------------------

def build_candidate_chain(facts: DeviceFacts) -> list[str]:
    """按硬件形态展开 ORT EP 候选链（设计文档 §5.2）。

    - NVIDIA 存在 → `CUDAExecutionProvider`（TensorRT 属 G-04/P3，需 `has_tensorrt`
      能力声明（T-902）才入链，本任务不入）；
    - Intel GPU → **走 OpenVINO 原生 API**，不是 ORT EP：ORT + OpenVINO EP 实测为
      **负收益**（比 ORT CPU EP 还慢），故刻意不入链；
    - CPU EP 恒在链尾（保底后端，任何环境都必须可用）。
    """
    chain: list[str] = []
    if facts.nvidia:
        chain.append("CUDAExecutionProvider")
    chain.append("CPUExecutionProvider")
    return chain


def deliberately_excluded(facts: DeviceFacts) -> list[BackendVerdict]:
    """**刻意排除**的后端：不是"验证失败"，而是"已验证为负收益/未纳入承诺"。

    与失败分开表达，避免让人误读成"这台机器有问题"。
    """
    out: list[BackendVerdict] = []
    out.append(
        BackendVerdict(
            provider="OpenVINOExecutionProvider",
            usable=False,
            reason_code="excluded_negative_gain",
            reason="实测 ORT + OpenVINO EP 比 ORT CPU EP 慢（负收益），本项目 Intel 路径走 OpenVINO 原生 API",
            note="实测 ORT + OpenVINO EP 比 ORT CPU EP 慢，故不采用该组合；"
                 "Intel GPU 走 OpenVINO 原生 API（不经 ORT）。",
        )
    )
    if not facts.nvidia:
        out.append(
            BackendVerdict(
                provider="TensorrtExecutionProvider",
                usable=False,
                reason_code="excluded_not_applicable",
                reason="未检测到 NVIDIA GPU；TensorRT 后端不适用",
                note="TensorRT 后端属 G-04（P3），且需 `has_tensorrt` 能力声明（T-902）。",
            )
        )
    return out


# ---------------------------------------------------------------------------
# 单后端验证
# ---------------------------------------------------------------------------

def _providers_arg(provider: str) -> list:
    """构造 ORT `providers=` 参数：目标 EP（含防御选项）+ CPU 兜底（避免重复项）。"""
    if provider == "CPUExecutionProvider":
        return ["CPUExecutionProvider"]
    return [_provider_entry(provider), "CPUExecutionProvider"]


def _provider_entry(provider: str):
    """构造单个 ORT provider 项（含因果性防御选项）。"""
    if provider == "CUDAExecutionProvider":
        # arena_extend_strategy 必须显式覆盖：默认 kNextPowerOfTwo 在 fp32 + 大 tile 下
        # 实测劣化 10.3 倍（P0 报告 §4）；cudnn_conv_algo_search 用 HEURISTIC 即可。
        return (provider, {
            "arena_extend_strategy": _ARENA_EXTEND_STRATEGY,
            "cudnn_conv_algo_search": "HEURISTIC",
        })
    return provider


def _feed_shape(sess, probe_size: int) -> tuple[str, tuple]:
    inp = sess.get_inputs()[0]
    shape: list[int] = []
    for i, d in enumerate(inp.shape):
        if isinstance(d, int) and d > 0:
            shape.append(d)
        elif i == 0:
            shape.append(1)  # 批维
        else:
            shape.append(probe_size)
    return inp.name, tuple(shape)


def _run_clean_latency(
    model_path: str,
    provider: str,
    temperature: tuple[str, tuple],
    runs: int = 3,
    *,
    ort_module=None,
) -> float | None:
    """在**不开启 profiling** 的独立 session 上测干净延迟。

    profiling 会显著放大单次耗时，用它排序后端会得出错误结论，所以另开一个 session。
    `ort_module` 与 `verify_backend` 同源注入（测试时可换成假 ORT）。
    """
    ort = ort_module
    if ort is None:
        import onnxruntime as ort  # type: ignore
    import numpy as np

    try:
        so = ort.SessionOptions()
        so.log_severity_level = 3
        sess = ort.InferenceSession(model_path, sess_options=so, providers=_providers_arg(provider))
        if provider not in sess.get_providers():
            return None
        name, shape = temperature
        x = np.random.default_rng(0).random(shape, dtype=np.float32)
        sess.run(None, {name: x})  # warmup
        samples = []
        for _ in range(max(1, runs)):
            t0 = time.perf_counter()
            sess.run(None, {name: x})
            samples.append((time.perf_counter() - t0) * 1000.0)
        samples.sort()
        return round(samples[len(samples) // 2], 2)
    except Exception as exc:
        logger.warning("干净延迟测量失败（不影响验证结论）：%s", exc)
        return None


def verify_backend(
    provider: str,
    model_path: str,
    *,
    probe_size: int = _DEFAULT_PROBE_SIZE,
    measure_latency: bool = True,
    ort_module=None,
) -> BackendVerdict:
    """对单个 EP 做真实性验证，返回 `BackendVerdict`。

    `ort_module` 可注入（测试用假 ORT 覆盖各判定分支）；默认惰性 import 真实 onnxruntime。
    **永不抛异常**——任何失败都表达为 `usable=False` 的 verdict。
    """
    if ort_module is None:
        try:
            import onnxruntime as ort_module  # type: ignore
        except ImportError as exc:
            return BackendVerdict(
                provider=provider,
                usable=False,
                reason_code="ort_not_installed",
                reason=f"onnxruntime 未安装（{exc}）；无法验证后端，按不可用处理",
                note="未安装 onnxruntime：无法做 EP 真实性验证。安装后重新探测即可。",
            )

    ort = ort_module
    if provider not in set(ort.get_available_providers()):
        return BackendVerdict(
            provider=provider,
            usable=False,
            reason_code="ep_not_available",
            reason=f"{provider} 不在 ORT 可用提供器中（该 wheel 未编译进此后端）",
            note=f"该 ORT 构建不含 {provider}。若硬件支持，需安装对应 wheel（如 onnxruntime-gpu）。",
        )

    if not os.path.isfile(model_path):
        return BackendVerdict(
            provider=provider,
            usable=False,
            reason_code="probe_model_missing",
            reason=f"验证用模型缺失：{model_path}",
            note="缺少可用于验证的最小模型文件，无法验证。",
        )

    # 1) 真建 session
    prof_dir = tempfile.mkdtemp(prefix="websr_ep_")
    so = ort.SessionOptions()
    so.log_severity_level = 3  # 保留 fallback warning 可见
    so.enable_profiling = True
    so.profile_file_prefix = os.path.join(prof_dir, "ep")
    try:
        sess = ort.InferenceSession(model_path, sess_options=so, providers=_providers_arg(provider))
    except Exception as exc:
        return BackendVerdict(
            provider=provider,
            usable=False,
            reason_code="session_create_failed",
            reason=f"session 创建失败：{type(exc).__name__}: {exc}",
            note=f"无法创建 session（{type(exc).__name__}）；该后端不可用，已按不可用处理。",
        )

    # 2) session 里真的有它吗
    active = list(sess.get_providers())
    if provider not in active:
        return BackendVerdict(
            provider=provider,
            usable=False,
            reason_code="ep_not_in_session_providers",
            reason=f"{provider} 未出现在 session providers（{active}）——EP 对象未创建",
            note=f"EP 对象未创建（session providers = {active}）。",
        )

    # 3) 🔴 关键：开 profiling 跑一次，数节点归属
    try:
        import numpy as np

        name, shape = _feed_shape(sess, probe_size)
        x = np.random.default_rng(0).random(shape, dtype=np.float32)
        sess.run(None, {name: x})
        profile_path = sess.end_profiling()
    except Exception as exc:
        return BackendVerdict(
            provider=provider,
            usable=False,
            reason_code="probe_run_failed",
            reason=f"验证推理执行失败：{type(exc).__name__}: {exc}",
            note=f"验证推理执行失败（{type(exc).__name__}）。",
        )

    assign = count_nodes_by_provider(profile_path)
    nodes = assign.get(provider, 0)
    cpu_nodes = assign.get("CPUExecutionProvider", 0)
    total = sum(assign.values())
    cpu_ratio = (cpu_nodes / total) if total else 0.0

    note_extra = ""
    if nodes == 1 and cpu_nodes == 0:
        note_extra = "（本后端仅 1 个节点，属整图融合成单个子图的正常现象；判据是 CPU 节点数为 0）"

    if nodes == 0:
        # 静默回退：必须记录为异常事件，通常意味着 DLL / 版本问题
        return BackendVerdict(
            provider=provider,
            usable=False,
            reason_code="silent_fallback",
            reason=f"静默回退：{provider} 执行 0 个节点，实际归属 {assign}",
            node_assignment=assign,
            node_count=0,
            cpu_node_count=cpu_nodes,
            note=f"⚠️ 静默回退（EP 存在但执行 0 节点）。实际节点归属：{assign}。"
                 "通常由 DLL 缺失或版本不匹配引起，不是正常情况。",
        )

    if provider != "CPUExecutionProvider" and cpu_nodes > 0:
        # 部分接管：记录占比；> 30% 视为不划算
        if cpu_ratio > _CPU_NODE_RATIO_LIMIT:
            return BackendVerdict(
                provider=provider,
                usable=True,
                reason_code="partial_unprofitable",
                reason=f"部分接管且不划算：CPU 节点占比 {cpu_ratio:.1%} > {_CPU_NODE_RATIO_LIMIT:.0%}",
                node_assignment=assign,
                node_count=nodes,
                cpu_node_count=cpu_nodes,
                adopted=False,
                note=f"CPU 节点占比 {cpu_ratio:.1%} 超过 {_CPU_NODE_RATIO_LIMIT:.0%}，"
                     "从候选链剔除（回退部分太多，整体不划算）。",
            )

    latency = None
    if measure_latency:
        latency = _run_clean_latency(model_path, provider, (name, shape), ort_module=ort)

    if provider == "CPUExecutionProvider":
        reason = f"{nodes} 节点全在 CPU（保底后端）"
        note = f"CPU 为保底后端：{nodes} 节点全在 CPU。CPU 路径强制 fp32，禁用 fp16。"
    else:
        reason = f"{nodes} 节点在本后端，CPU 节点 {cpu_nodes} 个"
        note = f"验证通过：{nodes} 节点在本后端，CPU 节点 {cpu_nodes} 个。{note_extra}".strip()

    return BackendVerdict(
        provider=provider,
        usable=True,
        reason_code="ok",
        reason=reason,
        node_assignment=assign,
        node_count=nodes,
        cpu_node_count=cpu_nodes,
        latency_ms=latency,
        adopted=True,
        note=note,
    )


# ---------------------------------------------------------------------------
# 全链验证
# ---------------------------------------------------------------------------

@dataclass
class VerificationResult:
    verdicts: list[BackendVerdict] = field(default_factory=list)
    adopted: list[str] = field(default_factory=list)
    backend_latency_ms: dict = field(default_factory=dict)
    exception_events: list[str] = field(default_factory=list)
    hardware_fingerprint: str = ""

    def adopted_backends(self) -> list[BackendVerdict]:
        return [v for v in self.verdicts if v.adopted]


def hardware_fingerprint(facts: DeviceFacts) -> str:
    """硬件指纹：决定 EP 验证结果何时失效。

    包含 GPU 名/驱动/显存 + ORT 版本 + CPU 名——驱动或 ORT 升级都会改变验证结论，
    必须使缓存失效（tech-arch §6.6 硬约束 1 的另一面）。
    """
    gpu = facts.primary_nvidia
    parts = [
        facts.cpu.name,
        f"nvidia={gpu.name}/drv={gpu.driver_version}/vram={gpu.vram_total_mb}" if gpu else "nvidia=-",
        f"intel={';'.join(g.full_name for g in facts.intel_gpu) or '-'}",
        f"ort={facts.ort_version or '-'}",
        f"os={facts.os_name}",
    ]
    return "|".join(parts)


def verify_candidates(
    facts: DeviceFacts,
    model_path: str,
    *,
    probe_size: int = _DEFAULT_PROBE_SIZE,
    measure_latency: bool = True,
    ort_module=None,
) -> VerificationResult:
    """按候选链逐个验证，返回全部 verdict（含**被剔除**的，附原因）。

    剔除规则：`latency > CPU 的 latency` → 可用但不划算，剔除（设计文档 §5.2）。
    """
    result = VerificationResult(hardware_fingerprint=hardware_fingerprint(facts))
    chain = build_candidate_chain(facts)

    for provider in chain:
        verdict = verify_backend(
            provider,
            model_path,
            probe_size=probe_size,
            measure_latency=measure_latency,
            ort_module=ort_module,
        )
        if verdict.latency_ms is not None:
            result.backend_latency_ms[provider] = verdict.latency_ms
        if verdict.reason_code == "silent_fallback":
            result.exception_events.append(
                f"{provider}: 静默回退（执行 0 节点，实际归属 {verdict.node_assignment}）"
            )
        result.verdicts.append(verdict)

    # 以 CPU 为基准剔除"能跑但更慢"的后端（只在两者都有干净延迟时比较）
    cpu_latency = result.backend_latency_ms.get("CPUExecutionProvider")
    if cpu_latency:
        for v in result.verdicts:
            if v.provider == "CPUExecutionProvider" or not v.adopted:
                continue
            if v.latency_ms and v.latency_ms > cpu_latency:
                v.adopted = False
                v.reason_code = "slower_than_cpu"
                v.reason = f"比 CPU 慢（{v.latency_ms}ms vs {cpu_latency}ms），从候选链剔除"
                v.note = (f"该后端可用但比 CPU EP 慢（{v.latency_ms}ms vs CPU {cpu_latency}ms），"
                          "从候选链剔除——静默使用会得到「看似加速实则更慢」的结果。")

    result.adopted = [v.provider for v in result.adopted_backends()]
    if not result.adopted:
        result.exception_events.append("候选链中无任何后端通过验证（将落到保底档）")
    return result
