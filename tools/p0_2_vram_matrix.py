"""Trustworthy peak-VRAM matrix: one subprocess per (precision, tile) config.

Why this exists
---------------
The first attempt reused one process for the whole matrix and sampled device
memory with pynvml. Two things went wrong and both are worth remembering:

1. ORT allocates its CUDA context, cuDNN handles and the device memory arena at
   *session creation* time, i.e. BEFORE the sampler's baseline was taken. So the
   baseline of every row after the first already contained the arena. The tiles
   that ran first looked like they needed ~4.7 GB and every later tile looked
   like it needed ~0 MB.

2. `nvmlDeviceGetComputeRunningProcesses()[i].usedGpuMemory` returns 0 on
   Windows WDDM, so a per-process figure is not available the way it is on
   Linux. `nvmlDeviceGetMemoryInfo().used` is device-wide and includes other
   applications.

The fix is to measure one configuration per process and take the baseline
*before* any ORT session exists:

    peak_delta = max(device_used during the runs) - device_used before the session

Parent mode (no --one) loops over the matrix and spawns itself with --one, so a
crash or OOM in one cell cannot take the rest down.

Usage:
    python tools/p0_2_vram_matrix.py --model M.onnx --model-fp16 M16.onnx \
        --tiles 512 384 256 --runs 3 --warmup 1 \
        --json .workbuddy/results/p0_2_vram_matrix.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# child mode: measure exactly one configuration
# ---------------------------------------------------------------------------
def _device_used_mb(nvml, handle) -> float:
    return nvml.nvmlDeviceGetMemoryInfo(handle).used / (1024 * 1024)


class _Sampler(threading.Thread):
    """Poll device-wide used memory and keep the maximum."""

    def __init__(self, nvml, handle, interval_s: float = 0.005):
        super().__init__(daemon=True)
        self.nvml = nvml
        self.handle = handle
        self.interval_s = interval_s
        self.peak = 0.0
        self.samples = 0
        self._stop = threading.Event()

    def run(self):
        while not self._stop.is_set():
            try:
                v = _device_used_mb(self.nvml, self.handle)
                if v > self.peak:
                    self.peak = v
                self.samples += 1
            except Exception:
                pass
            time.sleep(self.interval_s)

    def stop(self):
        self._stop.set()
        self.join(timeout=1.0)


def run_one(args) -> dict:
    path = args.model if args.precision == "fp32" else args.model_fp16
    if not path:
        return {"error": "no model given for this precision"}

    import pynvml

    pynvml.nvmlInit()
    handle = pynvml.nvmlDeviceGetHandleByIndex(0)
    gpu_name = pynvml.nvmlDeviceGetName(handle)
    total_mb = pynvml.nvmlDeviceGetMemoryInfo(handle).total / (1024 * 1024)

    # ---- the critical baseline: taken BEFORE any ORT session exists ---------
    baseline_mb = _device_used_mb(pynvml, handle)

    from _runtime_env import prepare, preload_ort_dlls

    prepare()
    preload_ort_dlls()
    import onnxruntime as ort

    so = ort.SessionOptions()
    so.log_severity_level = 3
    so.enable_mem_pattern = True
    so.enable_cpu_mem_arena = True
    if args.threads:
        so.intra_op_num_threads = args.threads

    providers = [p for p in args.providers]
    if "CUDAExecutionProvider" in providers:
        # ORT's defaults here are actively harmful on an 8 GB consumer card:
        #   cudnn_conv_algo_search=EXHAUSTIVE benchmarks every cuDNN algorithm and
        #   needs a large workspace per candidate, and arena_extend_strategy=
        #   kNextPowerOfTwo rounds the arena up to a power of two. On the RTX 3050
        #   the combination pushed peak device usage to ~98% and one fp32 tile-512
        #   run to 33 s -- 20x slower than the same shape with HEURISTIC.
        so.add_session_config_entry("session.use_device_allocator_for_initializers", "1")
        cuda_opts = {
            "cudnn_conv_algo_search": args.cudnn_algo,
            "arena_extend_strategy": args.arena,
            "do_copy_in_default_stream": "1",
        }
        if args.cudnn_workspace:
            cuda_opts["cudnn_conv_use_max_workspace"] = args.cudnn_workspace
        providers = [
            ("CUDAExecutionProvider", cuda_opts) if p == "CUDAExecutionProvider" else p
            for p in providers
        ]

    t0 = time.perf_counter()
    sess = ort.InferenceSession(path, sess_options=so, providers=providers)
    load_s = time.perf_counter() - t0
    active = sess.get_providers()

    after_session_mb = _device_used_mb(pynvml, handle)

    inp = sess.get_inputs()[0]
    shape = []
    for i, d in enumerate(inp.shape):
        if isinstance(d, int) and d > 0:
            shape.append(d)
        else:
            shape.append(1 if i in (0,) else args.tile)
    shape = tuple(shape)

    rng = np.random.default_rng(0)
    x = rng.random(tuple(s for s in shape if isinstance(s, int)), dtype=np.float32)
    if "float16" in inp.type:
        x = x.astype(np.float16)
    name = inp.name

    sampler = _Sampler(pynvml, handle)
    sampler.start()

    for _ in range(args.warmup):
        sess.run(None, {name: x})

    lat = []
    for _ in range(args.runs):
        t = time.perf_counter()
        out = sess.run(None, {name: x})
        lat.append((time.perf_counter() - t) * 1000.0)

    time.sleep(0.05)  # let the last allocation be observed
    sampler.stop()
    peak_mb = sampler.peak
    after_run_mb = _device_used_mb(pynvml, handle)

    lat_sorted = sorted(lat)
    p50 = lat_sorted[len(lat_sorted) // 2]
    p95 = lat_sorted[min(len(lat_sorted) - 1, int(round(0.95 * (len(lat_sorted) - 1))))]

    return {
        "precision": args.precision,
        "model": os.path.basename(path),
        "tile": args.tile,
        "input_shape": list(shape),
        "input_type": inp.type,
        "out_shape": list(out[0].shape),
        "load_s": round(load_s, 3),
        "active_providers": active,
        "config": {"cudnn_conv_algo_search": args.cudnn_algo, "arena_extend_strategy": args.arena},
        "latency_ms": {
            "n": len(lat),
            "mean": round(sum(lat) / len(lat), 1),
            "p50": round(p50, 1),
            "p95": round(p95, 1),
            "min": round(min(lat), 1),
            "max": round(max(lat), 1),
        },
        "vram": {
            "gpu": gpu_name,
            "total_mb": round(total_mb, 1),
            # clean baseline taken before the session existed
            "baseline_mb": round(baseline_mb, 1),
            "after_session_mb": round(after_session_mb, 1),
            "peak_mb": round(peak_mb, 1),
            "after_run_mb": round(after_run_mb, 1),
            # the number to quote: everything our process needed at peak
            "peak_delta_mb": round(max(0.0, peak_mb - baseline_mb), 1),
            # how much of that is the loaded model + arena floor
            "weights_and_context_mb": round(max(0.0, after_session_mb - baseline_mb), 1),
        },
        "out_mean": round(float(out[0].mean()), 6),
        "samples": sampler.samples,
    }


# ---------------------------------------------------------------------------
# parent mode: spawn one child per cell
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--model-fp16", default=None)
    ap.add_argument("--providers", nargs="+", default=["CUDAExecutionProvider", "CPUExecutionProvider"])
    ap.add_argument("--precisions", nargs="+", default=["fp32", "fp16"])
    ap.add_argument("--tiles", nargs="+", type=int, default=[512, 384, 256])
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--json", default=None)
    ap.add_argument(
        "--cudnn-algo",
        default="HEURISTIC",
        choices=["EXHAUSTIVE", "HEURISTIC", "DEFAULT"],
        help="ORT default is EXHAUSTIVE, which is pathological on 8 GB cards",
    )
    ap.add_argument(
        "--arena",
        default="kSameAsRequested",
        choices=["kNextPowerOfTwo", "kSameAsRequested"],
        help="ORT default is kNextPowerOfTwo (over-allocates)",
    )
    ap.add_argument("--cudnn-workspace", default=None, help="cudnn_conv_use_max_workspace 0/1")

    # child-only
    ap.add_argument("--one", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--precision", default="fp32", help=argparse.SUPPRESS)
    ap.add_argument("--tile", type=int, default=256, help=argparse.SUPPRESS)

    args = ap.parse_args()

    if args.one:
        sys.stdout.reconfigure(encoding="utf-8")
        res = run_one(args)
        print("__RESULT__" + json.dumps(res, ensure_ascii=False))
        return 0 if "error" not in res else 1

    results = []
    for precision in args.precisions:
        if precision == "fp16" and not args.model_fp16:
            continue
        for tile in args.tiles:
            cmd = [
                sys.executable, os.path.abspath(__file__),
                "--one",
                "--model", args.model,
                "--providers", *args.providers,
                "--precision", precision,
                "--tile", str(tile),
                "--runs", str(args.runs),
                "--warmup", str(args.warmup),
                "--threads", str(args.threads),
                "--cudnn-algo", args.cudnn_algo,
                "--arena", args.arena,
            ]
            if args.cudnn_workspace:
                cmd += ["--cudnn-workspace", args.cudnn_workspace]
            if args.model_fp16:
                cmd += ["--model-fp16", args.model_fp16]

            print(f"[{precision}] tile={tile} ... ", end="", flush=True)
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            res = None
            for line in (proc.stdout or "").splitlines():
                if line.startswith("__RESULT__"):
                    res = json.loads(line[len("__RESULT__"):])
            if res is None:
                res = {
                    "precision": precision,
                    "tile": tile,
                    "error": f"child exited {proc.returncode}: "
                             f"{(proc.stderr or proc.stdout or '')[-300:]}",
                }
                print(f"FAILED ({res['error'][:80]})")
            else:
                v = res["vram"]
                print(
                    f"{res['latency_ms']['mean']:>9.1f} ms   "
                    f"peak_delta={v['peak_delta_mb']:>8.1f} MB   "
                    f"(floor {v['weights_and_context_mb']:.0f} MB)"
                )
            results.append(res)

    out = {"results": results}
    if args.json:
        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2)
        print(f"\nwritten: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
