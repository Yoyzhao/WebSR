"""阶段 A：能力探测（tech-arch §6.1 / §6.2；跨环境设计文档 §4）。

**纯读事实，不做任何决策，不抛异常，不 import 推理库（惰性）**——
输出 `DeviceFacts` 可序列化，用于诊断导出与 bug 报告。

三条硬性要求（设计文档 §4.2，均为踩过的坑）：

1. **绝不抛异常**。任一探测项失败（pynvml 缺失、驱动过老、WMI 被禁）必须降级为
   "未知"并记录，而不是让整个启动流程崩掉。普通异常进 `probes_failed`；
   因**前置条件缺失**而跳过（如未安装 onnxruntime）进 `probes_skipped`——两者不可混同。
2. **绝不读取标称规格当可用值**。`vram_total_mb` 是标称，`vram_free_mb` 才是可用；
   门控与档位判定只允许用后者（本机标称 8 GB，实读可用 7.4 GB）。
3. **区分核显与独显，Intel GPU 必须读 `FULL_DEVICE_NAME`**。OpenVINO 的 GPU 插件会把
   NVIDIA 卡也枚举成 `(dGPU)`，用 `"GPU" in available_devices` 判断必然误判。

本模块只依赖标准库；`nvidia-smi` 走子进程，`onnxruntime` / `openvino` 走惰性 import。
"""

from __future__ import annotations

import ctypes
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field

logger = logging.getLogger("websr.engine.device_probe")

#: 单个外部命令的超时。探测本身不能成为启动瓶颈（设计文档 §4.2）。
_CMD_TIMEOUT_S = 5


# ---------------------------------------------------------------------------
# 数据结构（阶段 A 产物）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CpuFacts:
    name: str
    arch: str | None
    logical_cores: int | None
    physical_cores: int | None
    ram_total_mb: int | None
    ram_available_mb: int | None
    isa: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NvidiaGpuFacts:
    name: str
    driver_version: str | None
    compute_cap: str | None
    vram_total_mb: int | None
    vram_free_mb: int | None

    @property
    def supports_fp16(self) -> bool | None:
        """sm_53+ 具备 fp16 单元。**按能力判定，不按设备型号**（ADR-004 原则 2）。

        注意：这是"硬件是否具备 fp16 单元"，与"该精度在当前后端上是否划算"是两件事——
        后者是阶段 C/D 的结论（CPU 路径实测 fp16 无收益 → 恒用 fp32）。
        """
        if not self.compute_cap:
            return None
        try:
            major, minor = (int(x) for x in self.compute_cap.split(".")[:2])
        except Exception:
            return None
        return (major, minor) >= (5, 3)


@dataclass(frozen=True)
class IntelGpuFacts:
    full_name: str
    is_integrated: bool


@dataclass
class DeviceFacts:
    """阶段 A 全部事实（可 `asdict()` 序列化）。"""

    os_name: str
    is_wddm: bool
    cpu: CpuFacts
    nvidia: list[NvidiaGpuFacts] = field(default_factory=list)
    intel_gpu: list[IntelGpuFacts] = field(default_factory=list)
    ov_devices: dict = field(default_factory=dict)
    ort_available_providers: list[str] = field(default_factory=list)
    ort_version: str | None = None
    #: 探测**抛异常**的项（必须记录，设计文档 §4.2）
    probes_failed: list[str] = field(default_factory=list)
    #: 因前置缺失而**跳过**的项（如未安装 onnxruntime）——与失败区分，避免误读
    probes_skipped: list[str] = field(default_factory=list)
    #: 每项探测耗时（ms），用于确认"探测没成为启动瓶颈"
    probe_ms: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def primary_nvidia(self) -> NvidiaGpuFacts | None:
        """消费级单卡为目标场景，取第一张（多卡策略未定，属后续任务）。"""
        return self.nvidia[0] if self.nvidia else None

    @property
    def available_vram_mb(self) -> int | None:
        """实读可用显存；无独显 / 探测不到 → None（**不得回落标称值**）。"""
        gpu = self.primary_nvidia
        return gpu.vram_free_mb if gpu else None


# ---------------------------------------------------------------------------
# 探测项实现
# ---------------------------------------------------------------------------

def _probe_cpu() -> CpuFacts:
    name = os.environ.get("PROCESSOR_IDENTIFIER") or platform.processor() or "未知 CPU"
    arch = os.environ.get("PROCESSOR_ARCHITECTURE") or platform.machine() or None
    logical = os.cpu_count()

    physical: int | None = None
    isa: list[str] = []
    ram_total: int | None = None
    ram_avail: int | None = None

    # psutil 是可选增强（不是运行时依赖）：拿到物理核数
    try:
        import psutil  # type: ignore

        physical = psutil.cpu_count(logical=False)
        vm = psutil.virtual_memory()
        ram_total = int(vm.total / (1024 * 1024))
        ram_avail = int(vm.available / (1024 * 1024))
    except ImportError:
        pass  # CPU 物理核数/内存走下面的兜底

    if ram_total is None:
        ram_total, ram_avail = _system_memory_mb()

    return CpuFacts(
        name=name,
        arch=arch,
        logical_cores=logical,
        physical_cores=physical,
        ram_total_mb=ram_total,
        ram_available_mb=ram_avail,
        isa=isa,
    )


def _system_memory_mb() -> tuple[int | None, int | None]:
    """(total, available) MB。无第三方依赖的 best-effort 实现。"""
    try:
        if sys.platform == "win32":
            class _MEMSTAT(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MEMSTAT()
            stat.dwLength = ctypes.sizeof(_MEMSTAT)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):  # type: ignore[attr-defined]
                return None, None
            mb = 1024 * 1024
            return int(stat.ullTotalPhys / mb), int(stat.ullAvailPhys / mb)

        with open("/proc/meminfo", encoding="ascii") as fh:
            kb = {}
            for line in fh:
                k, _, v = line.partition(":")
                kb[k.strip()] = v.strip()
        total = int(kb["MemTotal"].split()[0]) // 1024
        avail = int(kb["MemAvailable"].split()[0]) // 1024
        return total, avail
    except Exception:
        return None, None


def read_system_memory_mb() -> tuple[int | None, int | None]:
    """(`total_mb`, `available_mb`) 的公开读取入口（阶段 E 水位采样复用，**永不抛异常**）。

    与阶段 A 共用同一实现，避免"启动时读到的内存"与"任务期读到的内存"来自两套口径。
    """
    try:
        return _system_memory_mb()
    except Exception as exc:
        logger.debug("物理内存读取失败（记 None）：%s", exc)
        return None, None


def _probe_nvidia() -> list[NvidiaGpuFacts]:
    """`nvidia-smi` 单次查询全部字段（多次调用会显著拉长探测时间）。

    `compute_cap` 是较新驱动才有的字段，老驱动会整条查询失败——此时退化为
    不带该字段的第二次查询（仍失败则记异常）。
    """
    exe = shutil.which("nvidia-smi")
    if not exe:
        raise RuntimeError("nvidia-smi 不存在（无 NVIDIA 驱动或未在 PATH 中）")

    def _query(fields: str) -> str:
        out = subprocess.run(
            [exe, "--query-gpu=" + fields, "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=_CMD_TIMEOUT_S,
        )
        if out.returncode != 0:
            raise RuntimeError(f"nvidia-smi 返回 {out.returncode}: {out.stderr.strip()[:200]}")
        return out.stdout.strip()

    try:
        raw = _query("name,driver_version,memory.total,memory.free,compute_cap")
    except Exception:
        # 老驱动无 compute_cap → 退化为四个字段
        raw = _query("name,driver_version,memory.total,memory.free")
        raw = "\n".join(line + ",?" for line in raw.splitlines() if line.strip())

    gpus: list[NvidiaGpuFacts] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        parts += ["?"] * (5 - len(parts))
        name, driver, total, free, cc = parts[:5]

        def _int(v: str) -> int | None:
            try:
                return int(float(v))
            except Exception:
                return None

        gpus.append(
            NvidiaGpuFacts(
                name=name or "未知 NVIDIA GPU",
                driver_version=None if driver in ("", "?") else driver,
                compute_cap=None if cc in ("", "?") else cc,
                vram_total_mb=_int(total),
                vram_free_mb=_int(free),
            )
        )
    if not gpus:
        raise RuntimeError("nvidia-smi 未返回任何 GPU 行")
    return gpus


def read_nvidia_free_mb() -> int | None:
    """主 NVIDIA GPU 当前空闲显存（MB）的公开读取入口，**永不抛异常**。

    与阶段 A 共用同一实现（`_probe_nvidia`），避免「面板的实读」与
    「探测的实读」来自两套口径（同 `read_system_memory_mb` 的纪律）。
    取不到（无 N 卡 / nvidia-smi 失败）返回 None，由调用方保留缓存值。
    """
    try:
        gpus = _probe_nvidia()
    except Exception:  # noqa: BLE001 - 无驱动/查询失败一律按"取不到"处理
        return None
    return gpus[0].vram_free_mb if gpus else None


def _probe_intel_gpu() -> list[IntelGpuFacts]:
    """Intel GPU 列表。**必须读 `FULL_DEVICE_NAME`**——OpenVINO 的 GPU 插件会把
    NVIDIA 卡也枚举成 `(dGPU)`，只看设备类型必然误判（设计文档 §4.1 / §11）。
    """
    import openvino as ov  # 惰性：未安装即抛 ImportError，由调用方归入 skipped

    core = ov.Core()
    found: list[IntelGpuFacts] = []
    for dev in core.available_devices:
        try:
            full = str(core.get_property(dev, "FULL_DEVICE_NAME"))
        except Exception:
            continue
        if not full:
            continue
        # 只认全名里明确是 Intel 的设备；"GPU" 前缀的枚举项不作依据
        if "intel" not in full.lower():
            continue
        dtype = ""
        try:
            dtype = str(core.get_property(dev, "DEVICE_TYPE"))
        except Exception:
            pass
        integrated = ("integrated" in dtype.lower()) or any(
            k in full.lower() for k in ("uhd graphics", "iris", "hd graphics")
        )
        found.append(IntelGpuFacts(full_name=full, is_integrated=integrated))
    return found


def _probe_ort() -> tuple[list[str], str | None]:
    """ORT 候选提供器（**仅候选，不作结论**——`get_available_providers()` 只说明
    "编译进去了"，是否真执行计算由阶段 B 的 profile 节点归属裁决）。"""
    import onnxruntime as ort  # 惰性

    return list(ort.get_available_providers()), getattr(ort, "__version__", None)


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------

def _step(name: str, fn, facts: DeviceFacts, *, skipped_reason: str | None = None):
    """执行单个探测项：计时 + 异常隔离。返回 `(ok, value)`。

    `skipped_reason` 用于"前置条件缺失"这类**非失败**的跳过。
    """
    t0 = time.perf_counter()
    try:
        value = fn()
    except ImportError as exc:
        # 依赖未安装属"跳过"而非"失败"——两者含义不同，不能混记
        facts.probes_skipped.append(f"{name}: 依赖未安装（{exc}）")
        logger.info("阶段 A 跳过 %s：依赖未安装", name)
        value = None
    except Exception as exc:
        facts.probes_failed.append(f"{name}: {type(exc).__name__}: {exc}")
        logger.warning("阶段 A 探测 %s 失败（已降级为未知）：%s", name, exc)
        value = None
    finally:
        facts.probe_ms[name] = round((time.perf_counter() - t0) * 1000, 2)
    if value is None and skipped_reason:
        facts.probes_skipped.append(f"{name}: {skipped_reason}")
    return value


def probe_device_facts() -> DeviceFacts:
    """阶段 A 入口。**永不抛异常**。"""
    facts = DeviceFacts(
        os_name=f"{platform.system()} {platform.release()}",
        # Windows 现代显示驱动模型即 WDDM：意味着拿不到**按进程**显存，
        # 只能读设备级 used 增量（P0 报告 §3.3）
        is_wddm=(sys.platform == "win32"),
        cpu=CpuFacts("未知 CPU", None, None, None, None, None),
    )

    cpu = _step("cpu", _probe_cpu, facts)
    if cpu:
        facts.cpu = cpu

    gpus = _step("nvidia", _probe_nvidia, facts)
    if gpus:
        facts.nvidia = gpus

    intel = _step("intel_gpu", _probe_intel_gpu, facts)
    if intel:
        facts.intel_gpu = intel

    ort_info = _step("onnxruntime", _probe_ort, facts)
    if ort_info:
        facts.ort_available_providers, facts.ort_version = ort_info

    logger.info(
        "阶段 A 完成：cpu=%s nvidia=%d intel=%d ort=%s 失败=%d 跳过=%d 耗时=%.1fms",
        facts.cpu.name[:40], len(facts.nvidia), len(facts.intel_gpu),
        facts.ort_version or "未安装", len(facts.probes_failed), len(facts.probes_skipped),
        sum(facts.probe_ms.values()),
    )
    return facts
