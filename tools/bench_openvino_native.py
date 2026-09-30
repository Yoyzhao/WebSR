"""OpenVINO native benchmark: ONNX -> IR conversion + inference on real devices.

Compares against ONNX Runtime on the same machine/model, so the "OpenVINO is
Nx faster" claim can be replaced with a measured number for *this* CPU.

Usage:
    python tools/bench_openvino_native.py --model data/models/RealESRGAN_x4.onnx \
        --devices CPU --tiles 512 --runs 5 --ir-dir data/models/ir
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402

from _bench_common import (  # noqa: E402
    RssSampler,
    VramSampler,
    bench,
    dump,
    make_test_image,
    tensor_fingerprint,
    tiled_infer,
)


def convert(model_path: str, ir_dir: str, compress_fp16: bool, tag: str) -> dict:
    import openvino as ov

    os.makedirs(ir_dir, exist_ok=True)
    core = ov.Core()
    t0 = time.perf_counter()
    ov_model = core.read_model(model_path)
    convert_s = time.perf_counter() - t0

    base = os.path.splitext(os.path.basename(model_path))[0] + tag
    xml_path = os.path.join(ir_dir, base + ".xml")

    t0 = time.perf_counter()
    ov.save_model(ov_model, xml_path, compress_to_fp16=bool(compress_fp16))
    save_s = time.perf_counter() - t0

    bin_path = xml_path.replace(".xml", ".bin")
    return {
        "read_s": round(convert_s, 3),
        "save_s": round(save_s, 3),
        "xml": xml_path,
        "xml_mb": round(os.path.getsize(xml_path) / 1048576, 3),
        "bin_mb": round(os.path.getsize(bin_path) / 1048576, 2),
        "compress_to_fp16": bool(compress_fp16),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--ir-dir", required=True)
    ap.add_argument("--devices", nargs="+", default=["CPU"])
    ap.add_argument("--tiles", nargs="+", type=int, default=[512])
    ap.add_argument(
        "--irs",
        nargs="+",
        default=["fp32", "fp16"],
        help="which IR variants to benchmark (conversion always produces both)",
    )
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--warmup", type=int, default=2)
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--cache-dir", default=None)
    ap.add_argument("--full-image", type=int, default=0)
    ap.add_argument("--aspect", type=float, default=16 / 9)
    ap.add_argument("--full-tile", type=int, default=512)
    ap.add_argument("--pad", type=int, default=10)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    import openvino as ov

    core = ov.Core()
    out: dict = {
        "openvino_version": ov.__version__,
        "available_devices": list(core.available_devices),
        "cpu_name": _cpu_name(),
        "args": vars(args),
    }
    print(f"openvino      : {ov.__version__}")
    print(f"devices       : {out['available_devices']}")

    out["ir_fp32"] = convert(args.model, args.ir_dir, False, "_fp32")
    out["ir_fp16"] = convert(args.model, args.ir_dir, True, "_fp16")
    print(f"IR fp32       : {out['ir_fp32']['bin_mb']} MB bin")
    print(f"IR fp16       : {out['ir_fp16']['bin_mb']} MB bin")

    results = []
    for ir_key in ("ir_fp32", "ir_fp16"):
        if ir_key.replace("ir_", "") not in args.irs:
            continue
        xml = out[ir_key]["xml"]
        for device in args.devices:
            if device.split(".")[0] not in [d.split(".")[0] for d in core.available_devices]:
                print(f"[skip] device {device} not available")
                continue
            model = core.read_model(xml)
            props = {}
            if args.threads > 0 and device == "CPU":
                props["INFERENCE_NUM_THREADS"] = args.threads
            if args.cache_dir:
                os.makedirs(args.cache_dir, exist_ok=True)
                props["CACHE_DIR"] = args.cache_dir

            t0 = time.perf_counter()
            compiled = core.compile_model(model, device, props or None)
            compile_s = time.perf_counter() - t0
            req = compiled.create_infer_request()
            in_name = compiled.input(0).get_any_name()

            for tile in args.tiles:
                x = make_test_image(tile, tile)

                def once():
                    return req.infer({in_name: x})[compiled.output(0)]

                rss = RssSampler().start()
                sampler = VramSampler().start()
                try:
                    timing, res = bench(once, warmup=args.warmup, runs=args.runs)
                except Exception as exc:
                    rss.stop()
                    sampler.stop()
                    msg = str(exc).replace("\n", " ")[:400]
                    results.append(
                        {
                            "ir": ir_key,
                            "device": device,
                            "tile": tile,
                            "compile_s": round(compile_s, 3),
                            "error": f"{type(exc).__name__}: {msg}",
                        }
                    )
                    print(f"[{ir_key} {device}] tile={tile:<4} FAILED: {msg[:160]}")
                    continue
                vram = sampler.stop()
                mem = rss.stop()

                entry = {
                    "ir": ir_key,
                    "device": device,
                    "tile": tile,
                    "compile_s": round(compile_s, 3),
                    "timing": timing.__dict__,
                    "ram": mem,
                    "vram": vram,
                    "fingerprint": tensor_fingerprint(res),
                }
                results.append(entry)
                print(
                    f"[{ir_key} {device}] tile={tile:<4} {timing.mean_ms:>9.2f} ms  "
                    f"p95={timing.p95_ms:>9.2f}  rss_delta={mem.get('delta_mb')} MB"
                )

    out["results"] = results

    if args.full_image:
        h = args.full_image
        w = int(round(h * args.aspect))
        ir_key = "ir_fp32"
        try:
            model = core.read_model(out[ir_key]["xml"])
            device = args.devices[0]
            compiled = core.compile_model(model, device)
            req = compiled.create_infer_request()
            in_name = compiled.input(0).get_any_name()
            img = make_test_image(h, w)
            rss = RssSampler().start()
            timing, res = bench(
                lambda: tiled_infer(
                    lambda c: req.infer({in_name: c})[compiled.output(0)],
                    img,
                    args.full_tile,
                    args.pad,
                ),
                warmup=1,
                runs=args.runs,
            )
            mem = rss.stop()
            out["full_image"] = {
                "ir": ir_key,
                "device": device,
                "tile": args.full_tile,
                "input_hw": [h, w],
                "output_hw": None if res is None else list(res.shape[-2:]),
                "timing": timing.__dict__,
                "ram": mem,
                "mpix_per_s": None
                if res is None
                else round((h * w / 1e6) / (timing.mean_ms / 1000.0), 3),
            }
            print(
                f"[full {ir_key} {device}] {h}x{w} tile={args.full_tile}  "
                f"{timing.mean_ms:.0f} ms  rss_delta={mem.get('delta_mb')} MB"
            )
        except Exception as exc:
            out["full_image"] = {
                "error": f"{type(exc).__name__}: {str(exc)[:300]}"
            }
            print(f"[full] FAILED: {str(exc)[:200]}")

    dump(out, args.json)
    return 0


def _cpu_name() -> str:
    try:
        import platform

        return platform.processor() or platform.machine()
    except Exception:
        return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
