"""T-803 引擎阶段 A/B 验证：能力探测 + EP 真实性验证 + 缓存 + 档位判定。

运行：./.venvs/sr-app/Scripts/python.exe scripts/test-script/verify_t803_engine.py

验证策略（为什么不是"跑一遍看着对"）：

- 阶段 B 的**判定分支**（静默回退 / EP 未创建 / session 失败 / 整图融合单节点 /
  部分接管）在真机上**无法逐一复现**——真机只有"能跑"和"不能跑"两种结果。
  所以用**可控假 ORT** 注入，把每个分支都真实走一遍；
- 真机部分只做**真实 ORT 的 CPU EP 端到端**（证明机制在真实 profile 上工作）；
- GPU 路径的真实证据（CUDA EP 1024 节点 / CPU 节点 0）另用 `.venvs/sr-gpu`
  独立复核，产出到 `.workbuddy/verify/t803/`（本脚本不依赖 GPU 环境）。

关键判据（tech-arch §6.1，不可简化）：
**目标 EP 节点数 > 0 且 CPU 节点数 = 0**——`节点数 == 1` 是整图融合的正常现象。
"""
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="websr_t803_"))
DATA = TMP / "data"
DATA.mkdir(parents=True, exist_ok=True)

# 注意：APP_DATA_DIR 必须在 import app 之前设置（config 会缓存）
os.environ["APP_DATA_DIR"] = str(DATA)
os.environ["APP_DB_URL"] = f"sqlite:///{(DATA / 'app.db').as_posix()}"

sys.path.insert(0, str(ROOT / "server"))

# 必须在 import onnxruntime **之前**完成 DLL 路径注册（与产品启动序列第 2 步同一条路径）。
# 否则 CUDA EP 会因找不到 cudart / cublasLt 而**静默回退 CPU**，本脚本就会把它误判成
# "这台机器上 CUDA 不可用"——2026-10-09 应用环境换入 GPU 版 ORT 后正是踩了这个坑。
from app.engine.runtime_env import prepare_dll_paths  # noqa: E402

prepare_dll_paths()

import numpy as np  # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


from app.engine import backend_cache, capabilities as caps_engine, ep_verify  # noqa: E402
from app.engine.device_probe import (  # noqa: E402
    CpuFacts,
    DeviceFacts,
    NvidiaGpuFacts,
    probe_device_facts,
)


# ---------------------------------------------------------------------------
# 可控假 ORT：把阶段 B 的判定分支全部走一遍
# ---------------------------------------------------------------------------

class _FakeSessionOptions:
    def __init__(self):
        self.log_severity_level = 0
        self.enable_profiling = False
        self.profile_file_prefix = ""


class _FakeSession:
    def __init__(self, providers, node_assignment, profile_path, latency_ms, run_error):
        self._providers = list(providers)
        self._assign = node_assignment
        self._profile = profile_path
        self._latency = latency_ms
        self._run_error = run_error

    def get_providers(self):
        return list(self._providers)

    def get_inputs(self):
        return [SimpleNamespace(name="images", shape=[1, 3, -1, -1], type="tensor(float)")]

    def run(self, _outputs, _feed):
        if self._run_error:
            raise self._run_error
        if self._latency:
            time.sleep(self._latency / 1000.0)
        return [np.zeros((1, 3, 8, 8), dtype=np.float32)]

    def end_profiling(self):
        return self._profile


class FakeOrt:
    """可配置假 onnxruntime：覆盖阶段 B 的全部判定分支。"""

    __version__ = "0.0-fake"

    def __init__(
        self,
        *,
        available_providers,
        session_providers=None,
        node_assignment=None,
        create_error=None,
        run_error=None,
        latency_ms=None,
        wrap_trace_events=False,
        assignment_by_target=None,
    ):
        self._available = list(available_providers)
        self._session_providers = session_providers
        self._assign = node_assignment or {}
        # 多后端场景下，CPU 会话自己也要有节点归属（否则会把 CPU 自己判成静默回退）
        self._assign_by_target = assignment_by_target or {}
        self._create_error = create_error
        self._run_error = run_error
        self._latency = latency_ms or {}
        self._wrap = wrap_trace_events

    def get_available_providers(self):
        return list(self._available)

    def SessionOptions(self):  # noqa: N802  （对齐 ORT 命名）
        return _FakeSessionOptions()

    def InferenceSession(self, _path, sess_options=None, providers=None):  # noqa: N802
        if self._create_error:
            raise self._create_error
        first = providers[0]
        target = first[0] if isinstance(first, tuple) else first

        assign = self._assign_by_target.get(target, self._assign)
        events = [{"cat": "Session", "args": {}}, {"cat": "Model", "args": {}}]  # 噪声事件
        for prov, n in assign.items():
            events += [{"cat": "Node", "args": {"provider": prov}} for _ in range(n)]

        fd, prof_path = tempfile.mkstemp(prefix="fakeprof_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"traceEvents": events} if self._wrap else events, fh)

        active = self._session_providers if self._session_providers is not None else self._available
        return _FakeSession(active, assign, prof_path, self._latency.get(target, 0), self._run_error)


def _dummy_model(name: str = "probe.onnx") -> str:
    p = TMP / name
    p.write_bytes(b"\x00" * 16)
    return str(p)


# ---------------------------------------------------------------------------
# 1. 阶段 A：能力探测
# ---------------------------------------------------------------------------
section("1. 阶段 A 能力探测（DeviceFacts）")

facts = probe_device_facts()
check("返回 DeviceFacts 且可序列化", isinstance(facts, DeviceFacts) and isinstance(facts.to_dict(), dict))
check("probes_failed 是列表（必须记录失败项）", isinstance(facts.probes_failed, list))
check("probes_skipped 与 failed 分离", isinstance(facts.probes_skipped, list))
check("probe_ms 记录每项耗时", isinstance(facts.probe_ms, dict) and "cpu" in facts.probe_ms)
check("探测总耗时 < 5s（不能成为启动瓶颈）", sum(facts.probe_ms.values()) < 5000,
      f"total={sum(facts.probe_ms.values())}ms")
check("cpu.name 非空", bool(facts.cpu.name and facts.cpu.name != "未知 CPU"))
check("system_ram 可读", facts.cpu.ram_total_mb and facts.cpu.ram_total_mb > 0,
      f"total={facts.cpu.ram_total_mb}")

if facts.nvidia:
    g = facts.primary_nvidia
    check("NVIDIA 事实齐全（名称/驱动/显存）",
          bool(g.name) and bool(g.driver_version) and g.vram_total_mb and g.vram_free_mb)
    check("可用显存 < 标称显存（**不把标称当可用**，§4.2 要求 2）",
          g.vram_free_mb <= g.vram_total_mb, f"free={g.vram_free_mb} total={g.vram_total_mb}")
    check("available_vram_mb 取自实读 free", facts.available_vram_mb == g.vram_free_mb)
    check("compute_cap 可解析为 fp16 能力（按能力不按型号）", isinstance(g.supports_fp16, bool))
else:
    check("无独显时 available_vram_mb 为 None（不回退标称值）", facts.available_vram_mb is None)

check("is_wddm 与平台一致（Windows → WDDM，拿不到按进程显存）",
      facts.is_wddm == (sys.platform == "win32"))

# 单项失败必须被隔离：注入会抛异常的探测
from app.engine import device_probe as dp  # noqa: E402

orig_nvidia = dp._probe_nvidia
try:
    dp._probe_nvidia = lambda: (_ for _ in ()).throw(RuntimeError("模拟驱动查询失败"))
    facts2 = dp.probe_device_facts()
    check("单项探测抛异常 → 记入 probes_failed 且整体不崩",
          any("nvidia" in f for f in facts2.probes_failed), f"failed={facts2.probes_failed}")
    check("失败后其余事实仍然产出（CPU 仍可读）", facts2.cpu.logical_cores is not None)
    check("失败项不影响 probes_failed 之外的结论", facts2.available_vram_mb is None)
finally:
    dp._probe_nvidia = orig_nvidia


# ---------------------------------------------------------------------------
# 2. profile 解析
# ---------------------------------------------------------------------------
section("2. ORT profile 节点归属解析")

fd, p_path = tempfile.mkstemp(suffix=".json")
with os.fdopen(fd, "w", encoding="utf-8") as fh:
    json.dump([
        {"cat": "Session", "args": {"provider": "x"}},  # 噪声：非 Node 类别必须被忽略
        {"cat": "Node", "args": {"provider": "CUDAExecutionProvider"}},
        {"cat": "Node", "args": {"provider": "CUDAExecutionProvider"}},
        {"cat": "Node", "args": {"provider": "CPUExecutionProvider"}},
    ], fh)
counts = ep_verify.count_nodes_by_provider(p_path)
check("只统计 cat=='Node' 的事件", counts == {"CUDAExecutionProvider": 2, "CPUExecutionProvider": 1},
      f"got={counts}")

fd, p_path2 = tempfile.mkstemp(suffix=".json")
with os.fdopen(fd, "w", encoding="utf-8") as fh:
    json.dump({"traceEvents": [
        {"cat": "Node", "args": {"provider": "OpenVINOExecutionProvider"}},
    ]}, fh)
check("兼容 traceEvents 包装形式", ep_verify.count_nodes_by_provider(p_path2) == {"OpenVINOExecutionProvider": 1})


# ---------------------------------------------------------------------------
# 3. 阶段 B 判定分支（假 ORT）
# ---------------------------------------------------------------------------
section("3. 阶段 B 判定分支（假 ORT 注入）")

PROBE = _dummy_model()

# 3.1 ORT 未安装
_saved = sys.modules.get("onnxruntime", "MISSING")
sys.modules["onnxruntime"] = None  # type: ignore[assignment]
v = ep_verify.verify_backend("CPUExecutionProvider", PROBE)
check("ORT 未安装 → usable=False / reason=ort_not_installed",
      not v.usable and v.reason_code == "ort_not_installed", f"got={v.reason_code}")
if _saved == "MISSING":
    sys.modules.pop("onnxruntime", None)
else:
    sys.modules["onnxruntime"] = _saved

# 3.2 EP 未编译进当前 wheel
v = ep_verify.verify_backend("CUDAExecutionProvider", PROBE,
                             ort_module=FakeOrt(available_providers=["CPUExecutionProvider"]))
check("EP 不在可用提供器 → ep_not_available", not v.usable and v.reason_code == "ep_not_available")

# 3.3 session 创建抛异常
v = ep_verify.verify_backend("CUDAExecutionProvider", PROBE,
                             ort_module=FakeOrt(available_providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
                                                create_error=RuntimeError("WinError 127")))
check("session 创建失败 → session_create_failed（不抛异常）",
      not v.usable and v.reason_code == "session_create_failed" and "WinError 127" in v.reason,
      f"got={v.reason_code}")

# 3.4 EP 对象未出现在 session
v = ep_verify.verify_backend("CUDAExecutionProvider", PROBE,
                             ort_module=FakeOrt(available_providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
                                                session_providers=["CPUExecutionProvider"]))
check("EP 对象未创建 → ep_not_in_session_providers",
      not v.usable and v.reason_code == "ep_not_in_session_providers", f"got={v.reason_code}")

# 3.5 🔴 静默回退：EP 在 session 里，但执行 0 节点
v = ep_verify.verify_backend("CUDAExecutionProvider", PROBE,
                             ort_module=FakeOrt(available_providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
                                                node_assignment={"CPUExecutionProvider": 1409}))
check("🔴 静默回退被检出（EP 在 session 里但 0 节点）",
      not v.usable and v.reason_code == "silent_fallback", f"got={v.reason_code}")
check("静默回退记录真实节点归属", v.cpu_node_count == 1409 and v.node_count == 0)
check("静默回退的说明里明确标注⚠️", "静默回退" in v.note)

# 3.6 整图融合：只有 1 个节点，但 CPU 节点为 0 → **必须判为通过**
v = ep_verify.verify_backend("OpenVINOExecutionProvider", PROBE,
                             ort_module=FakeOrt(available_providers=["OpenVINOExecutionProvider", "CPUExecutionProvider"],
                                                node_assignment={"OpenVINOExecutionProvider": 1}))
check("🔴 整图融合单节点（node=1, cpu=0）→ 判为**已接管**（不可误判为未接管）",
      v.usable and v.adopted and v.node_count == 1 and v.cpu_node_count == 0,
      f"usable={v.usable} nodes={v.node_count} cpu={v.cpu_node_count}")
check("融合情形的说明解释「节点数=1 是正常现象」", "整图融合" in v.note)

# 3.7 部分接管且 CPU 占比过高 → 剔除
v = ep_verify.verify_backend("CUDAExecutionProvider", PROBE,
                             ort_module=FakeOrt(available_providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
                                                node_assignment={"CUDAExecutionProvider": 10, "CPUExecutionProvider": 90}))
check("CPU 节点占比 90% > 30% → 可用但不采用（partial_unprofitable）",
      v.usable and not v.adopted and v.reason_code == "partial_unprofitable", f"got={v.reason_code}")

# 3.8 部分接管但占比可接受 → 采用
v = ep_verify.verify_backend("CUDAExecutionProvider", PROBE,
                             ort_module=FakeOrt(available_providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
                                                node_assignment={"CUDAExecutionProvider": 1000, "CPUExecutionProvider": 10}))
check("CPU 节点占比 1% → 采用", v.usable and v.adopted, f"got={v.reason_code}")

# 3.9 推理执行抛异常
v = ep_verify.verify_backend("CUDAExecutionProvider", PROBE,
                             ort_module=FakeOrt(available_providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
                                                node_assignment={"CUDAExecutionProvider": 10},
                                                run_error=RuntimeError("CUDA out of memory")))
check("验证推理失败 → probe_run_failed", not v.usable and v.reason_code == "probe_run_failed")

# 3.10 探针模型缺失
v = ep_verify.verify_backend("CPUExecutionProvider", str(TMP / "not-exist.onnx"),
                             ort_module=FakeOrt(available_providers=["CPUExecutionProvider"]))
check("探针模型缺失 → probe_model_missing", not v.usable and v.reason_code == "probe_model_missing")

# 3.11 可用但比 CPU 慢 → 从候选链剔除
_PER_TARGET = {
    "CUDAExecutionProvider": {"CUDAExecutionProvider": 1024},
    "CPUExecutionProvider": {"CPUExecutionProvider": 1409},
}
slow = FakeOrt(available_providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
               latency_ms={"CUDAExecutionProvider": 120, "CPUExecutionProvider": 40},
               assignment_by_target=_PER_TARGET)
facts_gpu = DeviceFacts(
    os_name="Test 1.0", is_wddm=False,
    cpu=CpuFacts("TestCPU", "x86_64", 8, 4, 32768, 16384),
    nvidia=[NvidiaGpuFacts("TestGPU", "1.0", "8.6", 8192, 7000)],
)
res = ep_verify.verify_candidates(facts_gpu, PROBE, probe_size=32, ort_module=slow)
check("候选链顺序 = [CUDA, CPU]", [v.provider for v in res.verdicts] ==
      ["CUDAExecutionProvider", "CPUExecutionProvider"])
check("GPU 比 CPU 慢 → 剔除（slower_than_cpu），只保留 CPU",
      res.adopted == ["CPUExecutionProvider"] and
      any(v.reason_code == "slower_than_cpu" for v in res.verdicts), f"adopted={res.adopted}")

# 3.12 GPU 更快 → 采用
fast = FakeOrt(available_providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
               latency_ms={"CUDAExecutionProvider": 40, "CPUExecutionProvider": 300},
               assignment_by_target=_PER_TARGET)
res_fast = ep_verify.verify_candidates(facts_gpu, PROBE, probe_size=32, ort_module=fast)
check("GPU 更快 → 采用 CUDA 优先", res_fast.adopted == ["CUDAExecutionProvider"] or
      res_fast.adopted[0] == "CUDAExecutionProvider", f"adopted={res_fast.adopted}")
check("静默回退时记入 exception_events",
      any("静默回退" in e for e in ep_verify.verify_candidates(
          facts_gpu, PROBE, probe_size=32,
          ort_module=FakeOrt(available_providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
                             node_assignment={"CPUExecutionProvider": 100})).exception_events))


# ---------------------------------------------------------------------------
# 4. 候选链与刻意排除
# ---------------------------------------------------------------------------
section("4. 候选链与刻意排除")

check("有 NVIDIA → [CUDA, CPU]", ep_verify.build_candidate_chain(facts_gpu) ==
      ["CUDAExecutionProvider", "CPUExecutionProvider"])
facts_cpu = DeviceFacts(os_name="Test 1.0", is_wddm=False,
                        cpu=CpuFacts("TestCPU", "x86_64", 8, 4, 32768, 16384))
check("无 NVIDIA → [CPU]", ep_verify.build_candidate_chain(facts_cpu) == ["CPUExecutionProvider"])
excl = ep_verify.deliberately_excluded(facts_gpu)
check("OpenVINO EP 被**刻意排除**并说明「负收益」",
      any(v.provider == "OpenVINOExecutionProvider" and "负收益" in v.reason for v in excl))
check("无 NVIDIA 时 TensorRT 也给出「不适用」说明",
      any(v.provider == "TensorrtExecutionProvider"
          for v in ep_verify.deliberately_excluded(facts_cpu)))


# ---------------------------------------------------------------------------
# 5. 档位判定（§6.2 规则表）
# ---------------------------------------------------------------------------
section("5. 档位判定（§6.2，实读可用显存 + 已验证后端）")


def _facts(vram_mb):
    return DeviceFacts(
        os_name="Test 1.0", is_wddm=False,
        cpu=CpuFacts("TestCPU", "x86_64", 8, 4, 65536, 32768),
        nvidia=([NvidiaGpuFacts("TestGPU", "1.0", "9.0", vram_mb, vram_mb)] if vram_mb else []),
    )


CASES = [
    ("T3", _facts(49152), ["CUDAExecutionProvider"]),
    ("T3", _facts(81920), []),                      # 规则 1 先于规则 3
    ("T2", _facts(24576), ["CUDAExecutionProvider"]),
    ("T2", _facts(16384), []),
    ("T1", _facts(8192), ["CUDAExecutionProvider"]),
    ("T0", _facts(8192), []),                       # 有卡但后端未验证 → 不得声明 T1
    ("T0", _facts(None), []),
    ("T1", _facts(None), ["CUDAExecutionProvider"]),  # 显存读不到但有已验证后端
]
ok = True
detail = []
for expect, f, adopted in CASES:
    got, _, _ = caps_engine.derive_tier(f, adopted)
    detail.append(f"{expect}<-vram={f.available_vram_mb},adopted={len(adopted)}/{got}")
    if got != expect:
        ok = False
check("§6.2 判定表 8 组全部命中", ok, " | ".join(detail))

t0, t0_label, t0_reason = caps_engine.derive_tier(_facts(8192), [])
check("有卡但未验证 → 理由明确指出「未通过验证」（不静默降级）",
      "未通过 EP 真实性验证" in t0_reason, f"reason={t0_reason}")
check("档位标签与档位对应", t0 == "T0" and t0_label.startswith("T0"))

# 禁止按设备型号硬编码（ADR-004 原则 2 / project-rules §2.2 铁律 3）
section("5b. 禁止按设备型号硬编码（源码级检查）")
MODEL_TOKENS = ("4090", "4080", "3090", "3080", "3050", "2070", "A100", "H100", "RTX ", "GTX ", "GeForce(R)")
violations = []
for f in ("device_probe.py", "ep_verify.py", "capabilities.py", "availability.py"):
    src = (ROOT / "server" / "app" / "engine" / f).read_text(encoding="utf-8")
    for tok in MODEL_TOKENS:
        # 只认代码里的字面量；注释/文档字符串中提及设备名不算违规
        for line in src.splitlines():
            code = line.split("#", 1)[0]
            if tok in code and '"""' not in line:
                violations.append(f"{f}: {line.strip()[:80]}")
check("引擎层代码不含设备型号分支", not violations, " | ".join(violations[:3]))


# ---------------------------------------------------------------------------
# 6. 缓存（tech-arch §6.6 硬约束 1）
# ---------------------------------------------------------------------------
section("6. EP 验证结果缓存")

cache_root = TMP / "cache_root"
cache_root.mkdir(exist_ok=True)
check("缓存路径 = <data_root>/calibration/verified_backends.json",
      backend_cache.cache_path(cache_root) == cache_root / "calibration" / "verified_backends.json")

res_fake = ep_verify.verify_candidates(facts_gpu, PROBE, probe_size=32, ort_module=fast)
p = backend_cache.save(cache_root, res_fake)
check("写入成功且文件存在", p is not None and p.is_file())
check("缓存内容含硬件指纹与采用后端",
      json.loads(p.read_text(encoding="utf-8"))["hardware_fingerprint"] == res_fake.hardware_fingerprint)

loaded = backend_cache.load(cache_root, res_fake.hardware_fingerprint)
check("指纹一致 → 命中", loaded is not None and loaded["adopted"] == res_fake.adopted)
check("指纹变化 → 作废（驱动/ORT 升级必须重跑）",
      backend_cache.load(cache_root, "different-fingerprint") is None)

p2 = backend_cache.save(cache_root, res_fake, simulated=True)
check("simulated=True → 拒绝写入（§6.3 约束 2）", p2 is None)

bad = backend_cache.cache_path(cache_root)
bad.write_text("{ this is not json", encoding="utf-8")
check("缓存损坏 → 返回 None（不抛异常）",
      backend_cache.load(cache_root, res_fake.hardware_fingerprint) is None)

# 命中缓存时 get_snapshot 不再跑 profile（cache_state=hit）
DATA_CACHE = TMP / "data_hit"
DATA_CACHE.mkdir(exist_ok=True)
c1, d1 = caps_engine.build_snapshot(DATA_CACHE, probe_model=PROBE, refresh=True, ort_module=fast, probe_size=32)
c2, d2 = caps_engine.build_snapshot(DATA_CACHE, probe_model=PROBE, refresh=False, ort_module=fast, probe_size=32)
check("首次构建写缓存", d1["cache_state"] == "written", f"got={d1['cache_state']}")
check("二次构建命中缓存（不重跑 profile）", d2["cache_state"] == "hit", f"got={d2['cache_state']}")
check("命中缓存后结论一致", c1["tier"] == c2["tier"] and c1["verified_backends"] == c2["verified_backends"])


# ---------------------------------------------------------------------------
# 7. 无探针模型时的降级
# ---------------------------------------------------------------------------
section("7. 无探针模型 → 优雅降级（不阻断启动）")

empty_root = TMP / "empty_root"
(empty_root / "models").mkdir(parents=True, exist_ok=True)
c3, d3 = caps_engine.build_snapshot(empty_root, models_dir=empty_root / "models", refresh=True)
check("无探针 → cache_state=skipped 且不抛异常", d3["cache_state"] == "skipped", f"got={d3['cache_state']}")
check("无探针 → 采用后端为空、档位 T0、保底档",
      d3["adopted_backends"] == [] and c3["tier"] == "T0" and c3["using_fallback"] is True)
check("无探针 → 记入 exception_events（不静默）",
      any("探针模型" in e for e in d3["exception_events"]), f"got={d3['exception_events']}")
check("无后端通过验证 → 不写缓存（避免把临时失败固化）",
      not backend_cache.cache_path(empty_root).exists())
check("Capabilities 契约字段齐全",
      set(c3) >= {"tier", "tier_label", "tier_reason", "device_facts", "verified_backends",
                  "ep_evidence", "using_fallback", "active_backend", "active_precision", "simulation"})
check("保底档恒 fp32（§6.8：不论 GPU/CPU）", c3["active_precision"] == "fp32")
check("档位模拟未启用（T-901 前）",
      c3["simulation"] == {"enabled": False, "force_tier": None})
check("ep_evidence 含被刻意排除的后端（负收益说明随面板展示）",
      any(e["provider"] == "OpenVINOExecutionProvider" for e in c3["ep_evidence"]))


# ---------------------------------------------------------------------------
# 8. 真实 ORT 端到端（CPU EP）
# ---------------------------------------------------------------------------
section("8. 真实 ORT 端到端（CPU EP，真实 profile）")

real_model = ROOT / "data" / "models" / "RealESRGAN_x4.onnx"
if not real_model.is_file():
    check("真实模型存在", False, f"missing {real_model}")
else:
    t0 = time.perf_counter()
    res_real = ep_verify.verify_candidates(facts, str(real_model), probe_size=64, measure_latency=True)
    dt = time.perf_counter() - t0
    cpu_v = next((v for v in res_real.verdicts if v.provider == "CPUExecutionProvider"), None)
    check("真实 CPU EP 验证通过", cpu_v is not None and cpu_v.usable and cpu_v.adopted)
    check("真实 profile 节点数 > 0 且在 CPU 名下",
          cpu_v.node_count > 0 and cpu_v.cpu_node_count == cpu_v.node_count,
          f"nodes={cpu_v.node_count} cpu={cpu_v.cpu_node_count}")
    check("CPU EP 干净延迟已测（用于与其他后端比较排序）",
          isinstance(cpu_v.latency_ms, (int, float)) and cpu_v.latency_ms > 0,
          f"latency={cpu_v.latency_ms}")
    check("真实验证总耗时在可接受范围（< 60s）", dt < 60, f"{dt:.1f}s")
    print(f"     CPU EP: {cpu_v.node_count} 节点 · 干净延迟 {cpu_v.latency_ms} ms · 总耗时 {dt:.1f}s")
    if facts.nvidia:
        cuda_v = next((v for v in res_real.verdicts if v.provider == "CUDAExecutionProvider"), None)
        if cuda_v is None:
            check("有 NVIDIA 卡时候选链包含 CUDA EP", False, "verdicts 里没有 CUDA")
        elif cuda_v.usable:
            # 2026-10-09：应用环境换入 GPU 版 ORT 后，这里由"必然不可用"变为"可能可用"。
            # 关键判据仍是 tech-arch §6.1：**节点数 > 0 且 CPU 节点数 = 0**。
            check("CUDA 可用时被真实采纳（节点数 > 0 且 CPU 节点 = 0）",
                  bool(cuda_v.adopted and cuda_v.node_count > 0 and cuda_v.cpu_node_count == 0),
                  f"adopted={cuda_v.adopted} nodes={cuda_v.node_count} "
                  f"cpu={cuda_v.cpu_node_count} reason={cuda_v.reason_code}")
            print(f"     CUDA EP: {cuda_v.node_count} 节点 · CPU 节点 {cuda_v.cpu_node_count} "
                  f"· 延迟 {cuda_v.latency_ms} ms")
        else:
            check("CUDA 不可用时不冒充可用（不采纳 + 有明确原因码）",
                  (not cuda_v.adopted) and bool(cuda_v.reason_code),
                  f"adopted={cuda_v.adopted} got={cuda_v.reason_code}")

    # 缓存复用 + 进程内快照
    real_root = TMP / "real_root"
    real_root.mkdir(exist_ok=True)
    rc1, rd1 = caps_engine.build_snapshot(real_root, probe_model=real_model, refresh=True, probe_size=64)
    rc2, rd2 = caps_engine.get_snapshot(real_root, probe_model=real_model, probe_size=64)
    check("真实快照首次写缓存", rd1["cache_state"] == "written")
    check("二次取快照走进程内缓存且结论一致",
          rd2["adopted_backends"] == rd1["adopted_backends"] and rc1["tier"] == rc2["tier"])
    check("真实指纹包含 GPU 名/驱动/显存 + ORT 版本",
          "nvidia=" in rd1["hardware_fingerprint"] and "ort=" in rd1["hardware_fingerprint"],
          f"fp={rd1['hardware_fingerprint'][:70]}")
    check("get_snapshot 返回的 details 不下发内部 _facts 对象", "_facts" not in rd2)


# ---------------------------------------------------------------------------
# 9. API 形状（隔离数据根）
# ---------------------------------------------------------------------------
section("9. /api/system/capabilities 形状（隔离数据根）")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

with TestClient(app) as client:
    r = client.get("/api/system/capabilities")
    check("200", r.status_code == 200)
    caps = r.json()
    check("顶层字段齐全", set(caps) >= {
        "tier", "tier_label", "tier_reason", "device_facts", "verified_backends",
        "ep_evidence", "using_fallback", "active_backend", "active_precision", "simulation"})
    check("device_facts 为实读值（非空且含显存字段）",
          "available_vram_gb" in caps["device_facts"] and "nominal_vram_gb" in caps["device_facts"])
    check("tier_reason 非空且可解释", bool(caps["tier_reason"]))

    r2 = client.get("/api/system/diagnostics")
    check("诊断导出含探测失败项与 EP 验证缓存状态",
          r2.status_code == 200 and
          {"probe", "ep_verification"} <= set(json.loads(r2.content.decode("utf-8"))))
    diag = json.loads(r2.content.decode("utf-8"))
    check("诊断含 probes_failed / cache_state / hardware_fingerprint",
          "probes_failed" in diag["probe"] and "cache_state" in diag["ep_verification"]
          and "hardware_fingerprint" in diag["ep_verification"])

    # 启动序列第 2 步（非阻塞）已完成
    # ⚠️ 该步在后台线程执行，是**刻意非阻塞**的（PRD/tech-arch §6.6）；断言必须
    # **有界等待**而非查一次瞬时值，否则会因调度时序偶发失败（测试竞态，非产品缺陷）。
    import time  # noqa: E402
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and getattr(app.state, "capability_report", None) is None:
        time.sleep(0.05)
    check("启动第 2 步写入 capability_report（非阻塞完成）",
          getattr(app.state, "capability_report", None) is not None)


# ---------------------------------------------------------------------------
print(f"\n===== 结果：{PASS} 通过 / {FAIL} 失败 =====")
shutil.rmtree(TMP, ignore_errors=True)
raise SystemExit(1 if FAIL else 0)
