"""T-801：轻量超分模型在**纯 CPU** 上的真实速度（OpenVINO 原生 API）。

回答的问题只有一个：**T0（纯 CPU）档的产品承诺是否成立**。

口径（与既有 P0 基线可比，别改）：
- 只走 **OpenVINO 原生 API**。**禁止**在 ORT 里注册 OpenVINO EP（实测慢 2.5 倍，
  见 `docs/tech/research/图像超分修复-P0实测报告.md`）。
- CPU 路径**只用 fp32**：桌面 CPU 没有 fp16 加速单元，fp16 只会更慢
  （P0 实测：Real-ESRGAN tile=256 fp32 4894 ms vs fp16 5936 ms）。
- 输入用**确定性合成图**（`_bench_common.make_test_image`），与 P0 基线同源。
- CPU 档的瓶颈是**物理内存**不是显存 → 记录峰值 RSS 增量，而不是 VRAM。

静态输入的模型（如 SAFMN，导出时用 `--static N`，输入必须"正好等于 N"）
会自动被识别，并且只在其 N 上测量——这正是产品侧 `decide_profile(fixed_tile=N)`
要表达的语义。

运行环境：`.venvs/sr-ov`（openvino 2026.4.0）。

用法：
    .venvs/sr-ov/Scripts/python.exe tools/bench_t801_light_models.py \
        --model SAFMN_x4=.workbuddy/verify/t801/safmn_x4_s256.onnx \
        --model SPAN_x2=.workbuddy/verify/t801/span_x2.onnx \
        --ir-dir .workbuddy/verify/t801/ir \
        --image 1280x720 --tiles 256 512 \
        --json .workbuddy/results/t801_light_cpu.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402

from _bench_common import (  # noqa: E402
    RssSampler,
    bench,
    dump,
    make_test_image,
    tensor_fingerprint,
    tiled_infer,
)


def parse_model(spec: str) -> tuple[str, str]:
    if "=" not in spec:
        raise argparse.ArgumentTypeError(f"--model 需要 NAME=PATH 形式，收到 {spec!r}")
    name, path = spec.split("=", 1)
    return name.strip(), path.strip()


def parse_image(spec: str) -> tuple[int, int]:
    w, _, h = spec.lower().partition("x")
    return int(h), int(w)


def static_input_size(compiled) -> tuple[int, int] | None:
    """若模型的输入形状是**静态**的，返回 (H, W)；否则 None。"""
    ps = compiled.input(0).get_partial_shape()
    if not ps.is_static:
        return None
    dims = [d.get_length() for d in ps]
    if len(dims) != 4:
        return None
    return int(dims[2]), int(dims[3])


def run_one(
    core,
    name: str,
    onnx: str,
    ir_dir: str,
    tiles: list[int],
    image_hw: tuple[int, int],
    warmup: int,
    runs: int,
) -> dict:
    import openvino as ov

    entry: dict = {"name": name, "onnx": onnx}

    ov_model = core.read_model(onnx)
    os.makedirs(ir_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(onnx))[0] + f"_{name}_fp32"
    xml = os.path.join(ir_dir, base + ".xml")
    t0 = time.perf_counter()
    ov.save_model(ov_model, xml, compress_to_fp16=False)
    entry["ir"] = {
        "xml": xml,
        "save_s": round(time.perf_counter() - t0, 3),
        "xml_mb": round(os.path.getsize(xml) / 1048576, 3),
        "bin_mb": round(os.path.getsize(xml.replace(".xml", ".bin")) / 1048576, 2),
        "compress_to_fp16": False,
    }

    t0 = time.perf_counter()
    compiled = core.compile_model(xml, "CPU")
    entry["compile_s"] = round(time.perf_counter() - t0, 3)
    req = compiled.create_infer_request()
    in_name = compiled.input(0).get_any_name()
    out_idx = compiled.output(0)

    static_hw = static_input_size(compiled)
    entry["static_input_hw"] = list(static_hw) if static_hw else None

    # scale 用一次探测得出（不猜）
    probe_n = static_hw[0] if static_hw else 64
    probe = make_test_image(probe_n, probe_n)
    out = np.asarray(req.infer({in_name: probe})[out_idx])
    scale = out.shape[-1] // probe_n
    entry["scale"] = int(scale)
    entry["probe_output_shape"] = list(out.shape)

    # ---- 单块延迟 ----
    entry["tiles"] = []
    for tile in tiles:
        if static_hw and tile != static_hw[0]:
            entry["tiles"].append(
                {
                    "tile": tile,
                    "skipped": f"静态输入要求正好 {static_hw[0]}（decide_profile(fixed_tile) 语义）",
                }
            )
            continue
        x = make_test_image(tile, tile)

        def once():
            return req.infer({in_name: x})[out_idx]

        rss = RssSampler().start()
        timing, res = bench(once, warmup=warmup, runs=runs)
        mem = rss.stop()
        out_px = (tile * scale) ** 2
        entry["tiles"].append(
            {
                "tile": tile,
                "latency": timing.__dict__,
                "output_px": out_px,
                "output_mpix_per_s": round(out_px / (timing.mean_ms / 1000.0) / 1e6, 3),
                "rss": mem,
                "fingerprint": tensor_fingerprint(res),
            }
        )
        print(
            f"  [{name}] tile={tile:<4} {timing.mean_ms:>8.1f} ms  "
            f"out={out_px / 1e6:.2f} MP  {out_px / (timing.mean_ms / 1000.0) / 1e6:>6.2f} MP/s  "
            f"rss+{(mem.get('delta_mb') or 0):.0f} MB"
        )

    # ---- 全图端到端 ----
    h, w = image_hw
    tile = None
    for cand in tiles:
        if static_hw:
            if cand == static_hw[0]:
                tile = cand
                break
        else:
            tile = cand
            break
    if tile is None:
        entry["full_image"] = {"skipped": "没有可用于该模型的 tile"}
        return entry

    img = make_test_image(h, w)

    def whole():
        return tiled_infer(lambda c: req.infer({in_name: c})[out_idx], img, tile)

    rss = RssSampler().start()
    timing, res = bench(whole, warmup=1, runs=1)
    mem = rss.stop()
    out_hw = list(res.shape[-2:])
    out_px = out_hw[0] * out_hw[1]
    in_px = h * w
    entry["full_image"] = {
        "input_hw": [h, w],
        "output_hw": out_hw,
        "tile": tile,
        "seconds": round(timing.mean_ms / 1000.0, 3),
        "input_mpix_per_s": round(in_px / (timing.mean_ms / 1000.0) / 1e6, 3),
        "output_mpix_per_s": round(out_px / (timing.mean_ms / 1000.0) / 1e6, 3),
        "rss": mem,
    }
    print(
        f"  [{name}] full {w}x{h} tile={tile} -> {out_hw[1]}x{out_hw[0]}  "
        f"{timing.mean_ms / 1000.0:.2f} s  rss_peak={mem.get('peak_mb')} MB"
    )
    return entry


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", action="append", default=[], type=parse_model,
                    help="NAME=PATH（可重复）")
    ap.add_argument("--ir-dir", required=True)
    ap.add_argument("--image", default="1280x720", help="全图尺寸 WxH")
    ap.add_argument("--tiles", nargs="+", type=int, default=[256, 512])
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--warmup", type=int, default=2)
    ap.add_argument("--threads", type=int, default=0, help="CPU 推理线程数（0=OpenVINO 默认）")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    import openvino as ov

    core = ov.Core()
    props = {"INFERENCE_NUM_THREADS": args.threads} if args.threads > 0 else None
    cpu_name = core.get_property("CPU", "FULL_DEVICE_NAME")

    out: dict = {
        "task": "T-801 轻量模型 CPU 速度",
        "openvino_version": ov.__version__,
        "cpu_full_name": cpu_name,
        "available_devices": list(core.available_devices),
        "backend": "OpenVINO 原生 API（非 ORT + OpenVINO EP）",
        "precision": "fp32（CPU 路径不使用 fp16）",
        "args": vars(args),
        "models": [],
    }
    print(f"openvino : {ov.__version__}")
    print(f"cpu      : {cpu_name}")
    print(f"devices  : {out['available_devices']}")

    for name, path in args.model:
        print(f"[{name}] {path}")
        try:
            out["models"].append(
                run_one(core, name, path, args.ir_dir, args.tiles,
                        parse_image(args.image), args.warmup, args.runs)
            )
        except Exception as exc:  # 单个模型失败不影响其它模型
            msg = str(exc).replace("\n", " ")[:400]
            print(f"  [{name}] FAILED: {type(exc).__name__}: {msg[:200]}")
            out["models"].append({"name": name, "onnx": path,
                                  "error": f"{type(exc).__name__}: {msg}"})

    # 与既有 Real-ESRGAN 基线放在一起，便于直接算倍数
    out["baseline_real_esrgan_cpu"] = {
        "source": ".workbuddy/results/ov_native_cpu.json + p0_3_ov_native_fullimage.json",
        "tile_256_ms": 4893.91,
        "tile_512_ms": 23661.06,
        "full_image": {"input_hw": [1080, 1920], "tile": 512, "seconds": 272.603,
                       "rss_peak_mb": 3893.7},
    }
    dump(out, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
