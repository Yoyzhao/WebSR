"""Convert a float32 ONNX model to float16 and verify it still runs.

Kept as a separate step (instead of converting inside the benchmark) so the
exact artifact under test is reproducible.

Usage:
    python tools/onnx_to_fp16.py <input.onnx> <output.onnx> [--keep-nodes ...]
"""

from __future__ import annotations

import argparse
import os

import numpy as np
import onnx
import onnxruntime as ort
from onnxconverter_common import float16

# Ops that must stay fp32 for numerical safety. RRDBNet has no such ops, but we
# keep the list explicit so the behaviour is visible and adjustable.
DEFAULT_BLOCK = ["Resize"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--block", nargs="*", default=DEFAULT_BLOCK)
    args = ap.parse_args()

    model = onnx.load(args.src)
    if args.block:
        print(f"[info] keeping these op types in fp32: {args.block}")

    print("[info] all_tensors_to_one_file=True so the .onnx stays self-contained")
    converted = float16.convert_float_to_float16(
        model, keep_io_types=False, op_block_list=list(args.block) or None,
        disable_shape_infer=False,
    )

    out_dir = os.path.dirname(os.path.abspath(args.dst))
    onnx.save(
        converted,
        args.dst,
        save_as_external_data=False,
    )

    print(f"[ok] wrote {args.dst} ({os.path.getsize(args.dst) / 1048576:.2f} MB)")

    # sanity: does it load and run?
    sess = ort.InferenceSession(args.dst, providers=["CPUExecutionProvider"])
    name = sess.get_inputs()[0].name
    in_type = sess.get_inputs()[0].type
    x = np.random.default_rng(0).random((1, 3, 64, 64))
    x = x.astype(np.float16) if "float16" in in_type else x.astype(np.float32)
    y = sess.run(None, {name: x})[0]
    print(f"[ok] ran on CPU EP, input_type={in_type} output {y.shape} "
          f"dtype={y.dtype} min={float(y.min()):.4f} max={float(y.max()):.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
