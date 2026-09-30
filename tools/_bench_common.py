"""Shared helpers for the super-resolution benchmark scripts.

No third-party imports at module level except numpy inside functions, so that
this file can be imported from any of the isolated venvs.
"""

from __future__ import annotations

import json
import statistics
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable


# --------------------------------------------------------------------------
# test data
# --------------------------------------------------------------------------
def make_test_image(h: int, w: int, seed: int = 1234):
    """Deterministic CHW float32 [0,1] image that exercises edges + texture.

    A flat or pure-random image would make tiling seams and kernel behaviour
    unrepresentative, so we combine a gradient, a high-frequency wave grid and
    mild noise.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    xx = xx.astype(np.float32)
    yy = yy.astype(np.float32)

    base = (xx / max(w - 1, 1)) * 0.45 + (yy / max(h - 1, 1)) * 0.30
    wave = 0.12 * np.sin(xx / 6.0) * np.cos(yy / 9.0)
    checker = 0.08 * (((xx // 16).astype(int) + (yy // 16).astype(int)) % 2)
    noise = rng.normal(0.0, 0.015, size=(h, w)).astype(np.float32)

    gray = np.clip(base + wave + checker + noise + 0.06, 0.0, 1.0).astype(np.float32)
    img = np.stack(
        [gray, np.clip(gray * 1.04, 0.0, 1.0), np.clip(gray * 0.96, 0.0, 1.0)], axis=0
    )
    return img[None, ...].astype(np.float32)


def tensor_fingerprint(arr) -> dict[str, float]:
    """Cheap correctness signal: shape + basic stats of the output."""
    import numpy as np

    a = np.asarray(arr, dtype=np.float32)
    return {
        "shape": list(a.shape),
        "sum": float(a.sum(dtype=np.float64)),
        "mean": float(a.mean(dtype=np.float64)),
        "min": float(a.min()),
        "max": float(a.max()),
    }


# --------------------------------------------------------------------------
# timing
# --------------------------------------------------------------------------
@dataclass
class Timing:
    n: int
    mean_ms: float
    p50_ms: float
    p95_ms: float
    min_ms: float
    max_ms: float
    stdev_ms: float
    fps: float

    @staticmethod
    def from_samples(ms: list[float]) -> "Timing":
        s = sorted(ms)
        n = len(s)
        mean = statistics.fmean(s)
        p95 = s[min(n - 1, int(round(0.95 * (n - 1))))]
        return Timing(
            n=n,
            mean_ms=round(mean, 2),
            p50_ms=round(statistics.median(s), 2),
            p95_ms=round(p95, 2),
            min_ms=round(s[0], 2),
            max_ms=round(s[-1], 2),
            stdev_ms=round(statistics.stdev(s), 2) if n > 1 else 0.0,
            fps=round(1000.0 / mean, 2) if mean > 0 else 0.0,
        )


def bench(fn: Callable[[], Any], warmup: int = 2, runs: int = 5) -> tuple[Timing, Any]:
    for _ in range(max(0, warmup)):
        fn()
    samples: list[float] = []
    out = None
    for _ in range(max(1, runs)):
        t0 = time.perf_counter()
        out = fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    return Timing.from_samples(samples), out


# --------------------------------------------------------------------------
# VRAM sampling
# --------------------------------------------------------------------------
class VramSampler:
    """Poll GPU memory from a background thread and keep the maximum.

    ORT does not expose peak device memory, so we sample. A 5 ms interval is
    fine for runs in the hundreds of milliseconds; it can miss a very short
    peak but is good enough for tier planning.
    """

    def __init__(self, interval_s: float = 0.005, device_index: int = 0):
        self.interval_s = interval_s
        self.device_index = device_index
        self.available = False
        self.baseline_mb = 0.0
        self.peak_mb = 0.0
        self.process_peak_mb = 0.0
        self.error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pynvml = None
        self._handle = None

    def _read(self):
        self._pynvml.nvmlInit()
        self._handle = self._pynvml.nvmlDeviceGetHandleByIndex(self.device_index)
        info = self._pynvml.nvmlDeviceGetMemoryInfo(self._handle)
        return info.used / (1024 * 1024)

    def _process_used_mb(self) -> float:
        try:
            import os

            pid = os.getpid()
            procs = self._pynvml.nvmlDeviceGetComputeRunningProcesses(self._handle)
            for p in procs:
                if p.pid == pid:
                    return p.usedGpuMemory / (1024 * 1024)
        except Exception:
            pass
        return 0.0

    def start(self):
        try:
            import pynvml

            self._pynvml = pynvml
            self.baseline_mb = self._read()
            self.available = True
        except Exception as exc:  # pragma: no cover - depends on host
            self.error = f"{type(exc).__name__}: {exc}"
            return self

        self._stop.clear()

        def loop():
            while not self._stop.is_set():
                try:
                    used = self._read()
                    if used > self.peak_mb:
                        self.peak_mb = used
                    pu = self._process_used_mb()
                    if pu > self.process_peak_mb:
                        self.process_peak_mb = pu
                except Exception:
                    pass
                time.sleep(self.interval_s)

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> dict[str, Any]:
        if not self.available:
            return {"available": False, "error": self.error}
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        delta = max(0.0, self.peak_mb - self.baseline_mb)
        return {
            "available": True,
            "baseline_mb": round(self.baseline_mb, 1),
            "peak_mb": round(self.peak_mb, 1),
            "delta_mb": round(delta, 1),
            "process_peak_mb": round(self.process_peak_mb, 1),
        }


# --------------------------------------------------------------------------
# system RAM sampling (matters for the CPU tier on a 16 GB machine)
# --------------------------------------------------------------------------
class RssSampler:
    """Sample this process' RSS from a background thread and keep the maximum."""

    def __init__(self, interval_s: float = 0.01):
        self.interval_s = interval_s
        self.available = False
        self.baseline_mb = 0.0
        self.peak_mb = 0.0
        self.error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc = None

    def _read(self) -> float:
        with self._proc.oneshot():
            return self._proc.memory_info().rss / (1024 * 1024)

    def start(self):
        try:
            import psutil

            self._proc = psutil.Process()
            self.baseline_mb = self._read()
            self.available = True
        except Exception as exc:  # pragma: no cover - depends on host
            self.error = f"{type(exc).__name__}: {exc}"
            return self

        self._stop.clear()

        def loop():
            while not self._stop.is_set():
                try:
                    used = self._read()
                    if used > self.peak_mb:
                        self.peak_mb = used
                except Exception:
                    pass
                time.sleep(self.interval_s)

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> dict[str, Any]:
        if not self.available:
            return {"available": False, "error": self.error}
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        return {
            "available": True,
            "baseline_mb": round(self.baseline_mb, 1),
            "peak_mb": round(self.peak_mb, 1),
            "delta_mb": round(max(0.0, self.peak_mb - self.baseline_mb), 1),
        }


# --------------------------------------------------------------------------
# tiled full-image runner (fixed tile shape, window-shift instead of resize)
# --------------------------------------------------------------------------
def tiled_infer(
    infer_fn: Callable[[Any], Any],
    img_chw: Any,
    tile: int,
    pad: int = 10,
) -> Any:
    """Run infer_fn over an image and stitch the result.

    Every tile has exactly shape (1, 3, tile, tile): when a window would fall
    outside the image we shift it back inside instead of padding, so the model
    always sees a fixed shape. That is what makes a static-shape ONNX usable
    for arbitrary image sizes without distorting the aspect ratio.
    """
    import numpy as np

    _, c, h, w = img_chw.shape
    scale = None
    out = None
    out_h = out_w = None

    for y0 in range(0, h, tile):
        for x0 in range(0, w, tile):
            y1, x1 = min(y0 + tile, h), min(x0 + tile, w)
            th, tw = y1 - y0, x1 - x0
            if th < tile:
                y0 = max(0, y1 - tile)
                th = min(tile, h)
            if tw < tile:
                x0 = max(0, x1 - tile)
                tw = min(tile, w)

            crop = img_chw[:, :, y0 : y0 + th, x0 : x0 + tw]
            if th < tile or tw < tile:
                padded = np.zeros((1, c, tile, tile), dtype=img_chw.dtype)
                padded[:, :, :th, :tw] = crop
                crop = padded

            res = np.asarray(infer_fn(crop), dtype=np.float32)
            if scale is None:
                scale = res.shape[-1] // tile
                out_h, out_w = h * scale, w * scale
                out = np.zeros((1, c, out_h, out_w), dtype=np.float32)

            oy, ox = y0 * scale, x0 * scale
            out[:, :, oy : oy + th * scale, ox : ox + tw * scale] = res[
                :, :, : th * scale, : tw * scale
            ]

    return out


# --------------------------------------------------------------------------
# cli helpers
# --------------------------------------------------------------------------
def dump(result: dict[str, Any], json_path: str | None = None) -> None:
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if json_path:
        with open(json_path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"\n[written] {json_path}")


def hm(seconds: float) -> str:
    return f"{seconds:.2f}s"
