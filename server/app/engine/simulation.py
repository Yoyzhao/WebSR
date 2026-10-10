"""T-901 档位模拟开关（控制面；PRD §2.3 原则 4 / ADR-004）。

## 为什么必须有它

T2/T3 的代码路径在本机（T1）**永远不会被执行到**——"写了但没跑过的代码 = 未验证代码"。
本模块让一台 T1 机器能**真实执行**高档位的**控制面**逻辑：档位判定、能力门控、
UI 置灰/解禁、参数传递、错误与降级链（PRD §2.2 B 段），把"无法验证"压缩到只剩
数据面的真实性能与显存行为。

⚠️ 此前前端曾在浏览器里把 `caps.tier` 改掉再存 localStorage —— 那是**假象**：
后端一无所知，真实决策/门控/降级链完全没走模拟档位。本模块取代之。

## 三条纪律（缺一不可）

1. **只覆盖「判定输入」，不伪造「硬件事实」**。真实的 `DeviceFacts` 一个字都不改
   （`/api/system/diagnostics` 里仍能看到真相）；模拟值只在**判定层**生效
   （档位推导 / 可用性门控 / 决策输入）。
2. **必须显式可辨**。生效后档位理由里必须出现"档位模拟"字样，界面据此提示
   "模拟档位仅用于测试，不代表真实能力"（PRD §2.3 原则 4）。
3. **P3 不得阻塞 T1 主链路**（PRD §7.1 硬要求 3）。任何解析失败一律当作"未开启"，
   **永不抛异常**。

纯度：只依赖标准库；"设置从哪来"由应用层 `services/simulation.py` 回答，
因此本模块的判据可在任意环境脱离应用栈单独复核。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .device_probe import DeviceFacts, NvidiaGpuFacts

#: 合法档位（与 `capabilities._LABELS` 同域）
VALID_TIERS = ("T0", "T1", "T2", "T3")

#: 显存上限（MB）：1 TB。纯粹是防呆上界——没人会模拟比这更大的卡。
_MAX_VRAM_MB = 1024 * 1024

#: 合成 GPU（本机无独显却强制显存时）的展示名，**必须**能一眼看出是模拟
_SYNTHETIC_GPU_NAME = "（模拟）本机无独显，显存为声明值"


@dataclass(frozen=True)
class SimulationOverride:
    """一次档位模拟的全部声明。`enabled=False` 时所有应用函数都是**恒等**的。

    - `force_tier`：直接指定档位（绕过显存推导）；
    - `force_vram_mb`：指定"可用显存"，参与档位推导**与**模型可用性门控；
    - `force_has_tensorrt`：把 TensorRT 加入 EP 候选链（否则恒不入链，见 G-04）。
    """

    enabled: bool = False
    force_tier: str | None = None
    force_vram_mb: int | None = None
    force_has_tensorrt: bool | None = None

    @property
    def active(self) -> bool:
        """是否真的改变了判定输入（开了开关但什么都没声明 = 没生效）。"""
        if not self.enabled:
            return False
        return (
            self.force_tier is not None
            or self.force_vram_mb is not None
            or self.force_has_tensorrt is not None
        )

    # -- 判定输入覆盖 -------------------------------------------------------

    def tier_or(self, real_tier: str) -> str:
        """档位：`force_tier` 优先，否则 `force_vram_mb` 参与推导由调用方处理。"""
        if self.active and self.force_tier:
            return self.force_tier
        return real_tier

    def vram_or(self, real_mb: int | None) -> int | None:
        """可用显存（MB）：模拟值优先。`0` 是**有效值**（模拟无显存），不能用 falsy 判。"""
        if self.active and self.force_vram_mb is not None:
            return self.force_vram_mb
        return real_mb

    def tensorrt_or(self, real: bool) -> bool:
        if self.active and self.force_has_tensorrt is not None:
            return self.force_has_tensorrt
        return real

    def apply_to_facts(self, facts: DeviceFacts | None) -> DeviceFacts | None:
        """产出**模拟视图**的 `DeviceFacts`（原对象不动）。

        只改"可用显存"这一项判定输入：有独显时替换其 `vram_free_mb`（保留真实的
        `compute_cap` 等能力事实），无独显而又声明了显存时**合成**一条记录
        （`compute_cap` 留空 → fp16 走因果性防御降为 fp32，不冒领能力）。

        刻意**不**改的（避免把模拟变成"假加速"）：设备名、驱动版本、CPU、ORT 提供器。
        """
        if not self.active or self.force_vram_mb is None or facts is None:
            return facts
        mb = int(self.force_vram_mb)
        if facts.nvidia:
            gpus = list(facts.nvidia)
            gpus[0] = replace(gpus[0], vram_free_mb=mb, vram_total_mb=mb)
            return replace(facts, nvidia=gpus)
        # 无独显却声明了显存：造一条**标注为模拟**的记录，让档位推导能看到它
        synth = NvidiaGpuFacts(
            name=_SYNTHETIC_GPU_NAME,
            driver_version=None,
            compute_cap=None,
            vram_total_mb=mb,
            vram_free_mb=mb,
        )
        return replace(facts, nvidia=[synth])

    # -- 可辨性 -------------------------------------------------------------

    def tier_reason(self, forced_tier: str, real_tier: str, real_reason: str) -> str:
        """强制档位的理由文案（**必须**带"档位模拟"字样，PRD §2.3 原则 4）。"""
        return (
            f"档位模拟：强制声明 {forced_tier}（本机真实档位 {real_tier}）。"
            f"模拟档位仅用于测试，不代表真实能力。真实判定依据：{real_reason}"
        )

    def summary(self) -> str:
        """一行摘要（写进任务 `reasons` 与诊断，便于事后解释"当时在模拟态"）。"""
        parts: list[str] = []
        if self.force_tier:
            parts.append(f"档位={self.force_tier}")
        if self.force_vram_mb is not None:
            parts.append(f"可用显存={self.force_vram_mb / 1024:.1f} GB")
        if self.force_has_tensorrt is not None:
            parts.append(f"TensorRT={'声明可用' if self.force_has_tensorrt else '声明不可用'}")
        return "；".join(parts) or "未声明任何覆盖项"

    def reason_line(self) -> str:
        """注入任务 `reasons` 的模拟说明（`active` 为假时为空串，调用方自行判空）。"""
        if not self.active:
            return ""
        return f"档位模拟生效（{self.summary()}）——执行结果不代表真实性能"

    def to_block(self) -> dict:
        """契约 `capabilities.simulation` 形状（字段集与契约 v1.0 一致，**不新增键**）。"""
        return {"enabled": bool(self.active), "force_tier": self.force_tier}


# ---------------------------------------------------------------------------
# 解析（**永不抛异常**：非法值一律当作"未声明"，P3 不得阻塞 T1 主链路）
# ---------------------------------------------------------------------------

def disabled() -> SimulationOverride:
    return SimulationOverride()


def parse_tier(raw: object) -> str | None:
    """`"t2"` / `" T2 "` → `"T2"`；其余（含空串）→ None。"""
    try:
        s = str(raw or "").strip().upper()
    except Exception:
        return None
    return s if s in VALID_TIERS else None


def parse_vram_mb(raw: object) -> int | None:
    """显存字符串 → MB。

    单位约定（**刻意不含糊**）：带 `G`/`GB` 后缀按 GB；**裸数字按 MB**
    （`"24576"` = 24 GB；`"24G"` = 24576 MB）。空串 / 非法 / 超界 → None
    （"未声明"），**不猜测**。
    """
    try:
        s = str(raw or "").strip().lower()
    except Exception:
        return None
    if not s:
        return None
    mult = 1
    for suffix in ("gb", "g"):
        if s.endswith(suffix):
            mult = 1024
            s = s[: -len(suffix)].strip()
            break
    try:
        num = float(s)
    except (TypeError, ValueError):
        return None
    if num < 0:
        return None
    mb = int(round(num * mult))
    if mb > _MAX_VRAM_MB:
        return None
    return mb


def parse_bool(raw: object) -> bool | None:
    """`true/false/1/0/yes/no/on/off` → bool；其余（含空串）→ None。"""
    try:
        s = str(raw or "").strip().lower()
    except Exception:
        return None
    if not s:
        return None
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off"):
        return False
    return None


def from_values(
    *, enabled: object = False, force_tier: object = None,
    force_vram_mb: object = None, force_has_tensorrt: object = None,
) -> SimulationOverride:
    """从**原始字符串**构造（设置表里存的就是字符串）。任一环节异常 → 未开启。"""
    try:
        on = parse_bool(enabled)
        return SimulationOverride(
            enabled=bool(on),
            force_tier=parse_tier(force_tier),
            force_vram_mb=parse_vram_mb(force_vram_mb),
            force_has_tensorrt=parse_bool(force_has_tensorrt),
        )
    except Exception:
        return disabled()
