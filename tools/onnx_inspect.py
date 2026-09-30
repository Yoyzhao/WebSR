"""Inspect an ONNX super-resolution model: opset, io names/shapes, dynamic axes.

Usage:
    python tools/onnx_inspect.py <model.onnx> [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import os

import onnx


def describe(path: str) -> dict:
    model = onnx.load(path, load_external_data=False)
    graph = model.graph

    def io_info(vals):
        out = []
        for v in vals:
            dims = []
            for d in v.type.tensor_type.shape.dim:
                if d.dim_param:
                    dims.append(d.dim_param)
                elif d.dim_value:
                    dims.append(int(d.dim_value))
                else:
                    dims.append(None)
            out.append(
                {
                    "name": v.name,
                    "elem_type": onnx.TensorProto.DataType.Name(
                        v.type.tensor_type.elem_type
                    ),
                    "shape": dims,
                }
            )
        return out

    ops = [n.op_type for n in graph.node]
    from collections import Counter

    op_counts = Counter(ops).most_common()

    # count parameters
    n_params = 0
    init_bytes = 0
    for init in graph.initializer:
        init_bytes += len(init.raw_data) if init.raw_data else 0
        n = 1
        for d in init.dims:
            n *= int(d)
        n_params += n

    return {
        "file": os.path.abspath(path),
        "size_mb": round(os.path.getsize(path) / 1048576, 2),
        "ir_version": model.ir_version,
        "producer": f"{model.producer_name} {model.producer_version}".strip(),
        "opset": [{"domain": o.domain or "ai.onnx", "version": o.version} for o in model.opset_import],
        "inputs": io_info(graph.input),
        "outputs": io_info(graph.output),
        "n_nodes": len(graph.node),
        "n_initializers": len(graph.initializer),
        "n_parameters": n_params,
        "initializer_bytes": init_bytes,
        "op_types": op_counts,
        "has_dynamic_hw": any(
            isinstance(d, str) for v in graph.input for d in _dims(v)
        ),
    }


def _dims(v):
    return [
        d.dim_param if d.dim_param else (int(d.dim_value) or None)
        for d in v.type.tensor_type.shape.dim
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("--json")
    args = ap.parse_args()

    info = describe(args.model)
    onnx.checker.check_model(args.model)
    info["checker"] = "passed"
    print(json.dumps(info, ensure_ascii=False, indent=2))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(info, fh, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
