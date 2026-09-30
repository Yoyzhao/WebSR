"""ONNX Runtime benchmark for the super-resolution pipeline.

Measures, per (model precision x tile size):
  - latency: mean / p50 / p95 over N runs after warmup
  - peak VRAM: sampled with pynvml, baseline-subtracted
  - output fingerprint, so a silent numerical break is visible

And, with --profile, the authoritative answer to "is the GPU really used?":
it parses the ORT profile JSON and counts executed nodes per execution
provider. `sess.get_providers()` alone does NOT prove the GPU ran anything.

Usage:
    python tools/bench_ort.py --model data/models/RealESRGAN_x4.onnx \
        --providers CUDAExecutionProvider CPUExecutionProvider \
        --tiles 256 384 512 768 --runs 5 --profile
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import onnxruntime as ort  # noqa: E402

from _bench_common import (  # noqa: E402
    Timing,
    VramSampler,
    bench,
    dump,
    make_test_image,
    tensor_fingerprint,
    tiled_infer,
)
from _runtime_env import prepare as prepare_dll_paths  # noqa: E402
from _runtime_env import preload_ort_dlls  # noqa: E402


def build_providers(names: list[str], cache_dir: str | None, intel_device: str):
    providers = []
    for n in names:
        if n == "CUDAExecutionProvider":
            providers.append(
                (
                    n,
                    {
                        "device_id": 0,
                        # HEURISTIC keeps init fast and deterministic; EXHAUSTIVE
                        # can be faster at runtime but its search is slow.
                        "cudnn_conv_algo_search": "HEURISTIC",
                        "arena_extend_strategy": "kSameAsRequested",
                        "do_copy_in_default_stream": "1",
                    },
                )
            )
        elif n == "OpenVINOExecutionProvider":
            opts = {"device_type": intel_device, "num_of_threads": "0"}
            if cache_dir:
                os.makedirs(cache_dir, exist_ok=True)
                opts["cache_dir"] = cache_dir
            providers.append((n, opts))
        else:
            providers.append((n, {}))
    return providers


def make_session(model_path, providers, threads=0, profile=False):
    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    if threads > 0:
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
    so.enable_profiling = bool(profile)
    t0 = time.perf_counter()
    sess = ort.InferenceSession(model_path, sess_options=so, providers=providers)
    load_s = time.perf_counter() - t0
    return sess, load_s


def parse_profile(profile_path: str) -> dict:
    """Count executed nodes per execution provider from an ORT profile JSON."""
    with open(profile_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    per_provider = Counter()
    for ev in data:
        if ev.get("cat") != "Node":
            continue
        args = ev.get("args") or {}
        prov = args.get("provider")
        if prov:
            per_provider[prov] += 1
    return dict(per_provider)


def input_cast(x, sess):
    it = sess.get_inputs()[0].type
    if "float16" in it:
        return x.astype(np.float16)
    return x.astype(np.float32)


def run_matrix(args) -> dict:
    model_path = os.path.abspath(args.model)
    fp16_path = os.path.abspath(args.model_fp16) if args.model_fp16 else None

    # Must happen before any InferenceSession: EP DLLs live in places Windows
    # does not search by default, and a failed EP load is a silent CPU fallback.
    dll_report = prepare_dll_paths()
    preloaded = preload_ort_dlls()

    variants: list[tuple[str, str]] = [("fp32", model_path)]
    if fp16_path and os.path.exists(fp16_path):
        variants.append(("fp16", fp16_path))

    providers = build_providers(args.providers, args.cache_dir, args.intel_device)

    env = {
        "onnxruntime_version": ort.__version__,
        "available_providers": ort.get_available_providers(),
        "providers_requested": args.providers,
        "providers_built": [p[0] for p in providers],
        "dll_dirs_registered": dll_report["added"],
        "dll_dirs_failed": dll_report["failed"],
        "ort_preload_dlls": preloaded,
        "cuda_libs": {},
        "args": vars(args),
        "cpu_count": os.cpu_count(),
    }
    for key in ("onnxruntime", "cuda"):
        pass
    try:
        import onnxruntime.capi._pybind_state as _st  # noqa: F401

        env["cuda_libs"]["use_cuda"] = bool(
            getattr(_st, "get_available_providers", None)
        )
    except Exception:
        pass

    results = []
    profile_info = None

    for label, path in variants:
        sess, load_s = make_session(
            path, providers, threads=args.threads, profile=args.profile
        )
        active = sess.get_providers()
        in_type = sess.get_inputs()[0].type

        # Loud check: a requested accelerator that is absent from get_providers()
        # means ORT could not create it and fell back. Silent fallback is the
        # single most misleading failure mode here, so never let it pass quietly.
        accel = [
            p
            for p in args.providers
            if p not in ("CPUExecutionProvider",) and p not in active
        ]
        if accel:
            print(
                f"  !! WARNING: requested {accel} but the session reports "
                f"{active} -- falling back to CPU. See the ORT log for the "
                f"provider-creation error."
            )

        if args.profile and profile_info is None:
            # The probe must match a shape the model actually accepts. For a
            # frozen (static-shape) graph the input dims are fixed, so derive
            # the probe size from the model instead of trusting --tile-probe.
            probe = args.tile_probe
            try:
                dims = sess.get_inputs()[0].shape
                if isinstance(dims[2], int) and isinstance(dims[3], int):
                    probe = dims[2]
            except Exception:
                pass
            x = input_cast(make_test_image(probe, probe), sess)
            sess.run(None, {sess.get_inputs()[0].name: x})
            pf = sess.end_profiling()
            try:
                profile_info = {
                    "model": os.path.basename(path),
                    "node_provider_assignment": parse_profile(pf),
                    "profile_file": pf,
                }
            except Exception as exc:
                profile_info = {"error": f"{type(exc).__name__}: {exc}"}

        # If the graph is static, only the matching tile size can be run.
        static_side = None
        try:
            dims = sess.get_inputs()[0].shape
            if isinstance(dims[2], int) and isinstance(dims[3], int):
                static_side = int(dims[2])
        except Exception:
            pass

        for tile in args.tiles:
            if static_side is not None and tile != static_side:
                print(
                    f"[{label}] tile={tile:<4} skipped: model is frozen to {static_side}px"
                )
                continue
            x = input_cast(make_test_image(tile, tile), sess)
            in_name = sess.get_inputs()[0].name

            # Never let one bad tile wipe out the whole run: an out-of-memory at
            # tile 512 previously killed the process before the JSON was written,
            # losing every measurement already collected.
            try:
                sampler = VramSampler().start()
                timing, out = bench(
                    lambda: sess.run(None, {in_name: x}),
                    warmup=args.warmup,
                    runs=args.runs,
                )
                vram = sampler.stop()
            except Exception as exc:
                sampler.stop()
                results.append(
                    {
                        "precision": label,
                        "model": os.path.basename(path),
                        "input_type": in_type,
                        "tile": tile,
                        "shape": [1, 3, tile, tile],
                        "active_providers": active,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                print(f"[{label}] tile={tile:<4} FAILED: {type(exc).__name__}: {exc}")
                continue

            results.append(
                {
                    "precision": label,
                    "model": os.path.basename(path),
                    "input_type": in_type,
                    "tile": tile,
                    "shape": [1, 3, tile, tile],
                    "out_shape": list(out[0].shape),
                    "load_s": round(load_s, 3),
                    "active_providers": active,
                    "timing": timing.__dict__,
                    "vram": vram,
                    "fingerprint": tensor_fingerprint(out[0]),
                }
            )
            print(
                f"[{label}] tile={tile:<4} {timing.mean_ms:>9.2f} ms  "
                f"p95={timing.p95_ms:>9.2f}  vram_delta={vram.get('delta_mb')} MB"
            )

    out = {"env": env, "results": results}
    if profile_info:
        out["profile"] = profile_info

    # ---- optional full-image tiled pipeline --------------------------------
    if args.full_image:
        try:
            h = args.full_image
            w = int(round(h * args.aspect))
            tile = args.full_tile
            label, path = variants[-1]
            sess, load_s = make_session(path, providers, threads=args.threads)
            in_name = sess.get_inputs()[0].name
            img = input_cast(make_test_image(h, w), sess)

            sampler = VramSampler().start()
            timing, out_img = bench(
                lambda: tiled_infer(
                    lambda c: sess.run(None, {in_name: c})[0], img, tile, args.pad
                ),
                warmup=1,
                runs=args.runs,
            )
            vram = sampler.stop()

            full = {
                "precision": label,
                "tile": tile,
                "input_hw": [h, w],
                "output_hw": None if out_img is None else list(out_img.shape[-2:]),
                "timing": timing.__dict__,
                "vram": vram,
                "fingerprint": None if out_img is None else tensor_fingerprint(out_img),
                "megapixels_in": round(h * w / 1e6, 3),
            }
            if out_img is not None:
                full["mpix_per_s"] = round(
                    (h * w / 1e6) / (timing.mean_ms / 1000.0), 3
                )
            out["full_image"] = full
            print(
                f"[full {label}] {h}x{w} tile={tile}  {timing.mean_ms:.0f} ms  "
                f"out={full['output_hw']}  vram_delta={vram.get('delta_mb')} MB"
            )
        except Exception as exc:
            # Keep everything measured so far; a failed full-image pass must not
            # discard the per-tile matrix.
            out["full_image"] = {"error": f"{type(exc).__name__}: {exc}"}
            print(f"[full] FAILED: {type(exc).__name__}: {exc}")

    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--model-fp16", default=None)
    ap.add_argument("--providers", nargs="+", default=["CPUExecutionProvider"])
    ap.add_argument("--tiles", nargs="+", type=int, default=[512])
    ap.add_argument("--tile-probe", type=int, default=256)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--warmup", type=int, default=2)
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--cache-dir", default=None)
    ap.add_argument("--intel-device", default="GPU")
    ap.add_argument("--profile", action="store_true")
    ap.add_argument("--full-image", type=int, default=0, help="e.g. 1080")
    ap.add_argument("--aspect", type=float, default=16 / 9)
    ap.add_argument("--full-tile", type=int, default=512)
    ap.add_argument("--pad", type=int, default=10)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    result = run_matrix(args)
    dump(result, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
