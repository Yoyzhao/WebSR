"""P0-1 smoke test: prove the CUDA EP actually EXECUTES, not just that it exists.

`session.get_providers()` only proves the EP object was constructed.  The only
trustworthy evidence that work really ran on the GPU is ORT's profiling JSON,
whose `cat:"Node"` events each carry `args.provider`.  This script therefore:

  1. registers the CUDA/cuDNN DLL directories (see _runtime_env),
  2. builds a session with CUDAExecutionProvider on a *static* 256x256 input,
  3. runs it once,
  4. parses the profile and prints how many nodes each provider executed.

A zero for CUDAExecutionProvider means ORT silently fell back to CPU.

Usage:
    python tools/p0_1_cuda_smoke.py [--model PATH] [--size 256]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _runtime_env import prepare, preload_ort_dlls  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse_profile(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    counts: dict[str, int] = {}
    for ev in data:
        if ev.get("cat") != "Node":
            continue
        prov = (ev.get("args") or {}).get("provider", "?")
        counts[prov] = counts.get(prov, 0) + 1
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=os.path.join(ROOT, "data", "models", "RealESRGAN_x4.onnx"))
    ap.add_argument("--size", type=int, default=256, help="square input side (must fit the model)")
    ap.add_argument("--no-dll-prep", action="store_true")
    args = ap.parse_args()

    if not args.no_dll_prep:
        prepare(verbose=False)
        preload_ort_dlls(verbose=False)

    import onnxruntime as ort

    print(f"ORT {ort.__version__}")
    print(f"available providers: {ort.get_available_providers()}")

    so = ort.SessionOptions()
    so.log_severity_level = 3  # only warnings/errors -- keep the fallback warning visible

    prof_dir = tempfile.mkdtemp(prefix="ortprof_")
    so.enable_profiling = True
    so.profile_file_prefix = os.path.join(prof_dir, "p01")

    sess = ort.InferenceSession(
        args.model, sess_options=so, providers=["CUDAExecutionProvider", "CPUExecutionProvider"]
    )
    active = sess.get_providers()
    print(f"session providers   : {active}")

    inp = sess.get_inputs()[0]
    print(f"model input         : {inp.name} {inp.shape} {inp.type}")

    # honour a static-shape model: the requested size must match if dims are fixed
    shape = []
    for i, d in enumerate(inp.shape):
        if isinstance(d, int) and d > 0:
            shape.append(d)
        elif i == 0:
            shape.append(1)
        else:
            shape.append(args.size)
    shape = tuple(shape)
    print(f"feeding shape       : {shape}")

    x = np.random.default_rng(0).random(shape, dtype=np.float32)
    y = sess.run(None, {inp.name: x})[0]
    print(f"output shape        : {y.shape}   mean={float(y.mean()):.6f}  sum={float(y.sum()):.4f}")

    prof_path = sess.end_profiling()
    counts = parse_profile(prof_path)
    # keep the profile next to the other results for later inspection
    out_dir = os.path.join(ROOT, ".workbuddy", "results")
    os.makedirs(out_dir, exist_ok=True)
    keep = os.path.join(out_dir, "p0_1_cuda_profile.json")
    with open(prof_path, "r", encoding="utf-8") as fh:
        raw = fh.read()
    with open(keep, "w", encoding="utf-8") as fh:
        fh.write(raw)

    print("\n--- node-level provider assignment (ground truth) ---")
    for prov, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {prov:34s} {n:5d} nodes")

    cuda_nodes = counts.get("CUDAExecutionProvider", 0)
    if "CUDAExecutionProvider" in active and cuda_nodes > 0:
        print("\nP0-1 RESULT: PASS - CUDA EP is genuinely executing on the GPU.")
    elif "CUDAExecutionProvider" not in active:
        print("\nP0-1 RESULT: FAIL - CUDA EP was not created; ORT fell back (see warning above).")
    else:
        print("\nP0-1 RESULT: FAIL - CUDA EP exists but executed 0 nodes (silent fallback).")

    print(f"\nprofile kept at: {keep}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
