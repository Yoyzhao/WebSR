"""阶段 C：首启自标定（F-10；tech-arch §6.1 阶段 C）。

回答的问题只有一个：**在这台机器上、对这个模型，tile 取多大、精度取哪种最划算？**
产出写进 `CALIBRATION` 表（`tile_curve` + `precision_decision`），阶段 D 据此把"自动"档
从**保底档**升级为真实参数（消费侧已由 T-804 实现，见 `system_info.read_calibration_view`）。

## 四条纪律

1. **一个 (tile × 精度) 组合一个 session，测完立即 `close()`**。ORT 的显存 arena 只增不减，
   同一 session 连跑多个 tile 会让后测的档位"看起来"不需要显存。2026-10-09 实测：
   每档新建并关闭后，同一 tile 重复测得的峰值增量偏差 < 2%（256 → 2070 / 2107 MB，
   512 → 6623 / 6670 MB），因此**不必为每档起子进程**（`tools/p0_2_vram_matrix.py` 的子进程
   隔离是为了让"设备级 used 增量"互不污染，这里靠 session 生命周期就达到了同样效果）。
2. **预算是硬边界，不是目标**。超预算即停在**当前已测出的最优档**，并如实标
   `budget_exceeded=true`。宁可给出"只测了 2 档"的真实结论，也不给一条猜出来的完整曲线。
3. **模拟结果绝不入库**。档位模拟（ADR-004）产出的记录标 `simulated=true`，
   由服务层拒绝写表——否则会被阶段 D 当成真实结论继承（§6.8 规则 2 同源）。
4. **单档失败不毁整轮**。OOM / 加载失败只记该档 `ok=false` 后继续或终止，
   绝不把整轮标定抛掉（`tools/bench_ort.py` 的注释记着一次 tile 512 OOM 直接把进程带走、
   JSON 都没写出来的教训）。

## 与阶段 E 的口径一致性

显存安全判据沿用阶段 E 的 `used / total`（**设备级**——Windows/WDDM 拿不到按进程用量），
阈值同为 **85%**。**刻意不另立一套口径**：否则"标定说这档安全、水位说它危险"会互相打架，
用户看到的提示也会自相矛盾。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .fallback import FALLBACK_MIN_TILE, FALLBACK_PRECISION, align_up
from .model_loader import LoadSpec, load_backend
from .watermark import is_oom_error, read_nvidia_memory_mb

logger = logging.getLogger(__name__)

# 标定的**搜索空间**（机制），不是产品参数：最终取值一律来自实测。
# 取几何递增 + 两个中间档，兼顾"低档保证成功"与"高档探索上限"。
TILE_SEARCH_SPACE: tuple[int, ...] = (64, 128, 256, 384, 512, 768, 1024)

DEFAULT_BUDGET_S = 8.0  # tech-arch §6.1：首次运行（异步，≤ 8 s 预算）
DEFAULT_RUNS = 2  # 每档正式测量次数（前面另有 warmup 次）
DEFAULT_WARMUP = 1
WATER_LEVEL_LIMIT = 0.85  # 与阶段 E 的降档阈值一致
DEFAULT_SCALE = 4

_FMT_BY_SUFFIX = {".onnx": "onnx", ".xml": "openvino_ir", ".param": "ncnn"}


class CalibrationCancelled(Exception):
    """调用方请求中止标定（映射为应用层的任务取消）。"""


# ---------------------------------------------------------------------------
# 结果结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TileSample:
    """单个 (tile × 精度) 组合的实测结果。"""

    tile: int
    precision: str
    ok: bool
    load_ms: float | None = None
    latency_ms: float | None = None  # 均值
    p95_ms: float | None = None
    throughput_pps: float | None = None  # 每秒像素（跨 tile 可比的唯一口径）
    peak_device_mb: float | None = None  # 采样到的设备级用量峰值
    peak_ratio: float | None = None  # peak / total
    vram_delta_mb: float | None = None  # 增量（扣基线），仅用于展示
    safe: bool = True  # 峰值是否低于水位阈值
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "tile": self.tile,
            "precision": self.precision,
            "ok": self.ok,
            "load_ms": self.load_ms,
            "latency_ms": self.latency_ms,
            "p95_ms": self.p95_ms,
            "throughput_pps": self.throughput_pps,
            "peak_device_mb": self.peak_device_mb,
            "peak_ratio": self.peak_ratio,
            "vram_delta_mb": self.vram_delta_mb,
            "safe": self.safe,
            "error": self.error,
        }


@dataclass(frozen=True)
class CalibrationOutcome:
    """一轮标定的完整结论。"""

    fingerprint: str
    model_path: str
    model_id: int | None
    tier: str
    backend: str
    scale: int
    samples: tuple[TileSample, ...] = ()
    recommended_tile: int | None = None
    precision_decision: str = FALLBACK_PRECISION
    recommended_tier: str | None = None
    reason: str = ""
    elapsed_ms: float = 0.0
    budget_exceeded: bool = False
    simulated: bool = False
    cancelled: bool = False

    # -- 落库 / 展示 -------------------------------------------------------

    def to_curve(self) -> dict:
        """→ `Calibration.tile_curve`。

        `recommended_tile` 是**阶段 D 唯一读取的字段**（见 `read_calibration_view`），
        其余字段供诊断与前端展示。
        """
        return {
            "recommended_tile": self.recommended_tile,
            "samples": [s.to_dict() for s in self.samples],
            "backend": self.backend,
            "scale": self.scale,
            "tier": self.tier,
            "model_path": self.model_path,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "budget_exceeded": self.budget_exceeded,
            "simulated": self.simulated,
            "reason": self.reason,
        }

    def to_dict(self) -> dict:
        return {
            "hardware_fingerprint": self.fingerprint,
            "model_id": self.model_id,
            "model_path": self.model_path,
            "tier": self.tier,
            "recommended_tier": self.recommended_tier or self.tier,
            "recommended_tile": self.recommended_tile,
            "precision_decision": self.precision_decision,
            "reason": self.reason,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "budget_exceeded": self.budget_exceeded,
            "simulated": self.simulated,
            "cancelled": self.cancelled,
            "samples": [s.to_dict() for s in self.samples],
            "curve": self.to_curve(),
        }

    def is_storable(self) -> bool:
        """能否作为**真实结论**入库。模拟结果与取消结果都不算。"""
        return (
            not self.simulated
            and not self.cancelled
            and self.recommended_tile is not None
            and any(s.ok for s in self.samples)
        )


# ---------------------------------------------------------------------------
# 采样线程
# ---------------------------------------------------------------------------


class _VramSampler(threading.Thread):
    """轮询设备级显存用量取峰值。**永不抛异常**（与阶段 E 同纪律）。"""

    def __init__(self, interval_s: float = 0.01) -> None:
        super().__init__(daemon=True)
        self._interval = interval_s
        self._stop = False
        self.peak_mb = 0.0
        self.total_mb: float | None = None
        self.base_mb: float | None = None

    def run(self) -> None:  # pragma: no cover - 线程体
        while not self._stop:
            try:
                used, total = read_nvidia_memory_mb()
                if used is not None:
                    self.peak_mb = max(self.peak_mb, float(used))
                if total is not None:
                    self.total_mb = float(total)
            except Exception:  # 观测永不致命
                pass
            time.sleep(self._interval)

    def stop(self) -> None:
        self._stop = True
        self.join(timeout=2.0)


# ---------------------------------------------------------------------------
# 候选集
# ---------------------------------------------------------------------------


def candidate_tiles(
    *,
    align: int = 1,
    fixed_tile: int | None = None,
    space: tuple[int, ...] = TILE_SEARCH_SPACE,
    min_tile: int = FALLBACK_MIN_TILE,
) -> list[int]:
    """候选块边长。

    - **静态输入模型**（`fixed_tile` 非空）只有一个合法尺寸——不是"最小档"，是"唯一档"；
    - 动态模型按 `align` 向上对齐（只收紧不放宽，与 `model_constraints` 同口径）。
    """
    if fixed_tile:
        return [int(fixed_tile)]
    a = max(1, int(align or 1))
    out: list[int] = []
    for t in space:
        if t < min_tile:
            continue
        v = align_up(int(t), a)
        if v not in out:
            out.append(v)
    return out or [align_up(FALLBACK_MIN_TILE, a)]


# ---------------------------------------------------------------------------
# 单档测量
# ---------------------------------------------------------------------------


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return ordered[idx]


def _measure_one(
    *,
    model_path: Path,
    fmt: str,
    tile: int,
    precision: str,
    backend: str,
    scale: int,
    runs: int,
    warmup: int,
    should_cancel=None,
) -> TileSample:
    """测一个组合。**独立 session，测完立即 close**（纪律 1）。异常一律转成样本字段。"""
    base_used, _total = read_nvidia_memory_mb()
    sampler = _VramSampler()
    sampler.base_mb = float(base_used) if base_used is not None else None
    load_ms: float | None = None
    latencies: list[float] = []
    backend_obj = None

    try:
        t0 = time.perf_counter()
        spec = LoadSpec(
            path=Path(model_path),
            fmt=fmt,
            precision=precision,
            backend=backend,
            scale=scale,
        )
        backend_obj = load_backend(spec)
        load_ms = (time.perf_counter() - t0) * 1000.0

        sampler.start()
        rng = np.random.default_rng(0)  # 固定种子：同一台机器上结果可复现
        x = rng.random((1, 3, tile, tile), dtype=np.float32)

        for _ in range(max(0, warmup)):
            backend_obj.infer(x)

        for _ in range(max(1, runs)):
            if should_cancel is not None and should_cancel():
                raise CalibrationCancelled("标定被取消")
            t1 = time.perf_counter()
            backend_obj.infer(x)
            latencies.append((time.perf_counter() - t1) * 1000.0)
    except CalibrationCancelled:
        raise
    except BaseException as exc:  # noqa: BLE001 - OOM 也要变成一条数据
        sampler.stop()
        if backend_obj is not None:
            try:
                backend_obj.close()
            except Exception:
                pass
        kind = "OOM" if is_oom_error(exc) else type(exc).__name__
        return TileSample(
            tile=tile, precision=precision, ok=False, load_ms=load_ms,
            error=f"{kind}: {str(exc)[:200]}", safe=False,
        )
    finally:
        if sampler.is_alive():
            sampler.stop()
        if backend_obj is not None:
            try:
                backend_obj.close()
            except Exception:
                pass

    if not latencies:
        return TileSample(
            tile=tile, precision=precision, ok=False, load_ms=load_ms,
            error="预算耗尽，未完成测量", safe=False,
        )

    mean_ms = sum(latencies) / len(latencies)
    peak = sampler.peak_mb or None
    total = sampler.total_mb
    ratio = (peak / total) if (peak and total) else None
    delta = None
    if peak is not None and sampler.base_mb is not None:
        delta = max(0.0, peak - sampler.base_mb)

    return TileSample(
        tile=tile,
        precision=precision,
        ok=True,
        load_ms=round(load_ms, 1) if load_ms is not None else None,
        latency_ms=round(mean_ms, 1),
        p95_ms=round(_percentile(latencies, 0.95) or mean_ms, 1),
        # 吞吐是跨 tile 唯一可比的口径：大 tile 单次更慢但每像素可能更划算
        throughput_pps=round((tile * tile * scale * scale) / (mean_ms / 1000.0), 1),
        peak_device_mb=round(peak, 1) if peak is not None else None,
        peak_ratio=round(ratio, 4) if ratio is not None else None,
        vram_delta_mb=round(delta, 1) if delta is not None else None,
        safe=(ratio is None or ratio <= WATER_LEVEL_LIMIT),
        error=None,
    )


# ---------------------------------------------------------------------------
# 推荐
# ---------------------------------------------------------------------------


def _recommend(samples: list[TileSample]) -> tuple[int | None, str]:
    """在**安全**且成功的档位里取吞吐最高者。返回 (tile, 理由)。"""
    usable = [s for s in samples if s.ok and s.safe and s.throughput_pps]
    if not usable:
        unsafe = [s for s in samples if s.ok and not s.safe]
        if unsafe:
            return None, (
                f"所有测得的档位都超过显存水位 {WATER_LEVEL_LIMIT:.0%}"
                f"（最低 {min(s.peak_ratio or 0 for s in unsafe):.0%}），"
                "退回保底档更稳妥"
            )
        return None, "没有成功测得任何档位，退回保底档"

    best = max(usable, key=lambda s: (s.throughput_pps or 0.0))
    detail = "，".join(
        f"{s.tile}px {s.throughput_pps / 1000:.0f}k px/s" for s in sorted(usable, key=lambda x: x.tile)
    )
    return best.tile, (
        f"在显存水位 ≤ {WATER_LEVEL_LIMIT:.0%} 的前提下取吞吐最高档："
        f"{best.tile}px（{best.throughput_pps / 1000:.0f}k px/s）。实测：{detail}"
    )


def _decide_precision(samples: list[TileSample], preferred: str) -> tuple[str, str]:
    """精度判定：fp16 只有在**同档实测吞吐更高**时才采纳。

    刻意不做"fp16 一律更省显存所以更好"的推断——那是开发机的结论，不是这台机器的。
    """
    by_tile: dict[int, dict[str, TileSample]] = {}
    for s in samples:
        if s.ok:
            by_tile.setdefault(s.tile, {})[s.precision] = s
    for tile in sorted(by_tile):
        pair = by_tile[tile]
        if "fp16" in pair and "fp32" in pair:
            f16, f32 = pair["fp16"], pair["fp32"]
            if (f16.throughput_pps or 0) > (f32.throughput_pps or 0) * 1.05:
                return (
                    "fp16",
                    f"同档实测 fp16 吞吐更高（{f16.throughput_pps / 1000:.0f}k vs "
                    f"{f32.throughput_pps / 1000:.0f}k px/s），采纳 fp16",
                )
            return (
                "fp32",
                f"同档实测 fp16 未更快（{f16.throughput_pps / 1000:.0f}k vs "
                f"{f32.throughput_pps / 1000:.0f}k px/s），按 fp32 走更稳妥",
            )
    return preferred or FALLBACK_PRECISION, f"只测到 {preferred or FALLBACK_PRECISION}，精度不变"


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def run_calibration(
    *,
    model_path: Path | str,
    fingerprint: str,
    tier: str = "T0",
    model_id: int | None = None,
    backend: str = "CPUExecutionProvider",
    scale: int = DEFAULT_SCALE,
    align: int = 1,
    fixed_tile: int | None = None,
    precisions: tuple[str, ...] = (FALLBACK_PRECISION,),
    budget_s: float = DEFAULT_BUDGET_S,
    runs: int = DEFAULT_RUNS,
    warmup: int = DEFAULT_WARMUP,
    simulate: bool = False,
    force_tiles: tuple[int, ...] | None = None,
    should_cancel=None,
    report=None,
) -> CalibrationOutcome:
    """跑一轮标定。

    `simulate=True` 只产出**结构完整但标记 simulated** 的结论，供控制面验证与前端联调；
    调用方必须据此拒绝落库（`is_storable()` 返回 False）。

    `report` 为可选进度回调 `(stage: str, message: str) -> None`。
    """
    started = time.perf_counter()
    path = Path(model_path)
    fmt = _FMT_BY_SUFFIX.get(path.suffix.lower(), "onnx")

    def _notify(stage: str, message: str) -> None:
        if report is not None:
            try:
                report(stage, message)
            except Exception:  # 上报失败不影响标定
                pass

    if simulate:
        outcome = _simulate_outcome(
            model_path=path, fingerprint=fingerprint, tier=tier, model_id=model_id,
            backend=backend, scale=scale, align=align,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
        )
        _notify("calibrating", "档位模拟：产出标记 simulated 的结论，不入正式标定表")
        return outcome

    if not path.is_file():
        return CalibrationOutcome(
            fingerprint=fingerprint, model_path=str(path), model_id=model_id, tier=tier,
            backend=backend, scale=scale,
            reason=f"标定模型不存在：{path}", simulated=False,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
        )

    tiles = list(force_tiles) if force_tiles else candidate_tiles(align=align, fixed_tile=fixed_tile)
    deadline = started + max(0.5, float(budget_s))
    samples: list[TileSample] = []
    budget_exceeded = False

    _notify("calibrating", f"开始标定：候选块边长 {tiles}，精度 {list(precisions)}，预算 {budget_s:.0f}s")

    for precision in precisions:
        for tile in tiles:
            if should_cancel is not None and should_cancel():
                return CalibrationOutcome(
                    fingerprint=fingerprint, model_path=str(path), model_id=model_id,
                    tier=tier, backend=backend, scale=scale, samples=tuple(samples),
                    reason="标定被取消", cancelled=True,
                    elapsed_ms=(time.perf_counter() - started) * 1000.0,
                )
            # 预算决定"**还能不能再测一档**"，不截断单档测量本身：
            # 一档测到一半停下只会换来一条无用的失败样本（2026-10-09 实测踩到）。
            # 因此首档无条件测完——"测一档"是标定的最小有意义单位。
            if samples and time.perf_counter() >= deadline:
                budget_exceeded = True
                break

            sample = _measure_one(
                model_path=path, fmt=fmt, tile=tile, precision=precision, backend=backend,
                scale=scale, runs=runs, warmup=warmup,
                should_cancel=should_cancel,
            )
            samples.append(sample)
            if sample.ok:
                _notify(
                    "calibrating",
                    f"{precision} tile={tile} → {sample.latency_ms:.0f}ms，"
                    f"吞吐 {sample.throughput_pps / 1000:.0f}k px/s，"
                    f"显存峰值 {sample.peak_ratio:.0%}" if sample.peak_ratio is not None
                    else f"{precision} tile={tile} → {sample.latency_ms:.0f}ms",
                )
            else:
                _notify("calibrating", f"{precision} tile={tile} 失败：{sample.error}")

            if not sample.ok and sample.error and "OOM" in sample.error:
                # OOM 说明已越界：更大的档只会更糟，本精度到此为止
                break
            if sample.ok and not sample.safe:
                # 超过水位阈值：不再往上探（纪律：只收紧不放宽）
                _notify(
                    "calibrating",
                    f"tile={tile} 显存峰值 {sample.peak_ratio:.0%} 超过水位阈值，停止上探"
                    if sample.peak_ratio is not None else f"tile={tile} 超过水位阈值，停止上探",
                )
                break

        if budget_exceeded:
            break

    if time.perf_counter() >= deadline:
        budget_exceeded = True

    recommended, rec_reason = _recommend(samples)
    precision_decision, prec_reason = _decide_precision(samples, precisions[0] if precisions else FALLBACK_PRECISION)
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    budget_note = "（预算耗尽，结论基于已测档位）" if budget_exceeded else ""
    reason = f"{rec_reason}；{prec_reason}{budget_note}"

    return CalibrationOutcome(
        fingerprint=fingerprint,
        model_path=str(path),
        model_id=model_id,
        tier=tier,
        backend=backend,
        scale=scale,
        samples=tuple(samples),
        recommended_tile=recommended,
        precision_decision=precision_decision,
        recommended_tier=tier,
        reason=reason,
        elapsed_ms=elapsed_ms,
        budget_exceeded=budget_exceeded,
    )


def _simulate_outcome(
    *, model_path: Path, fingerprint: str, tier: str, model_id: int | None,
    backend: str, scale: int, align: int, elapsed_ms: float,
) -> CalibrationOutcome:
    """档位模拟：**不跑推理**，产出一条 `simulated=true` 的示意结论。

    存在的意义是让前端与控制面在**没有真实标定**的环境下也能联调
    （ADR-004 的档位模拟口径）。取值刻意停在保底档，**不假装标定过**。
    """
    from .fallback import fallback_tile

    tile = fallback_tile(align)
    sample = TileSample(
        tile=tile, precision=FALLBACK_PRECISION, ok=True,
        latency_ms=None, throughput_pps=None, safe=True,
        error=None,
    )
    return CalibrationOutcome(
        fingerprint=fingerprint, model_path=str(model_path), model_id=model_id, tier=tier,
        backend=backend, scale=scale, samples=(sample,),
        recommended_tile=tile, precision_decision=FALLBACK_PRECISION, recommended_tier=tier,
        reason="档位模拟结果（非实测），仅供控制面与前端联调，不得写入标定表",
        elapsed_ms=elapsed_ms, simulated=True,
    )
