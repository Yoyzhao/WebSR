"""Freeze a dynamic-shape SR model into a fixed-shape copy.

Our pipeline design is "fixed tile size + window shift", which lets us use a
static-shape graph. That is both the fastest configuration and, empirically,
the one some execution providers actually accept: the OpenVINO EP assigned
zero nodes on the dynamic-shape model and silently fell back to the CPU EP.

Usage:
    python tools/onnx_freeze_shape.py <in.onnx> <out.onnx> --size 512
"""

from __future__ import annotations

import argparse
import os

import numpy as np
import onnx
import onnxruntime as ort


def freeze(path_in: str, path_out: str, size: int, scale: int = 4) -> None:
    model = onnx.load(path_in)
    graph = model.graph

    def set_static(value_info, dims):
        shape = value_info.type.tensor_type.shape
        del shape.dim[:]
        for d in dims:
            shape.dim.add().dim_value = int(d)

    set_static(graph.input[0], [1, 3, size, size])
    set_static(graph.output[0], [1, 3, size * scale, size * scale])

    # ask ONNX to propagate the new shapes through the graph
    try:
        model = onnx.shape_inference.infer_shapes(model)
    except Exception as exc:
        print(f"[warn] shape inference failed (continuing): {exc}")

    onnx.checker.check_model(model)
    onnx.save(model, path_out)
    print(f"[ok] {os.path.basename(path_out)}  {os.path.getsize(path_out)/1048576:.2f} MB")

    # verify the frozen model actually runs and produces the expected shape
    sess = ort.InferenceSession(path_out, providers=["CPUExecutionProvider"])
    x = np.random.default_rng(0).random((1, 3, size, size)).astype(np.float32)
    y = sess.run(None, {sess.get_inputs()[0].name: x})[0]
    assert y.shape == (1, 3, size * scale, size * scale), y.shape
    print(f"[ok] ran: {x.shape} -> {y.shape}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--size", type=int, default=512)
    ap.add_argument("--scale", type=int, default=4)
    args = ap.parse_args()
    freeze(args.src, args.dst, args.size, args.scale)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
