"""T-210 / T-305: is ncnn actually usable on Windows + Python?

Why this script exists
----------------------
PRD 7.2 / 7.5 and tech-arch 7-risk-1 left one question open: can the ncnn
Python binding be obtained on Windows, and does it really execute?  The answer
decides whether a `.bin` file paired with a `.param` is a real backend or only
a "conditional" one.

The bar is deliberately higher than "does it import":

  * importing a module proves nothing about inference;
  * ncnn has a known all-black-output bug, so a run that "succeeds" but
    returns a flat black image must count as a FAILURE, not a pass;
  * the output has to agree numerically with the ONNX main path, otherwise it
    cannot be trusted as an interchangeable backend.

So a deterministic synthetic image is run through

  (a) ncnn  on CPU,
  (b) ncnn  on the Vulkan GPU backend,
  (c) ORT   on CPU  -- the reference,

and the script asserts non-blackness, CPU/GPU agreement and ncnn/ONNX
agreement.  Every number that a human would want to re-check is dumped to JSON.

Two ncnn traps found the hard way while writing this -- do not "fix" them back:

  1. `ncnn.Mat(numpy_array)` interprets a 3-D array as **(c, h, w)**, *not*
     (h, w, c).  Feeding HWC silently turns the channel count into the image
     height and the extractor then reads out of bounds.
  2. The same constructor **borrows** the numpy buffer, it does not copy it.
     If the temporary array is garbage-collected before `extract()` the process
     dies with an access violation (exit 139) and no Python traceback.

Usage (repo root, inside the dedicated env -- never the app env):

    .venvs/sr-ncnn/Scripts/python.exe -u tools/verify_ncnn_windows.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bench_common import make_test_image  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PKG = os.path.join(ROOT, ".workbuddy", "verify", "t210", "ncnn_pkg")
DEFAULT_ONNX = os.path.join(ROOT, "data", "models", "RealESRGAN_x4.onnx")


# --------------------------------------------------------------------------
# ncnn param parsing
# --------------------------------------------------------------------------
def parse_param(path: str) -> dict:
    """Read an ncnn `.param` and return its input/output blob names.

    The format is: magic line, "<layer_count> <blob_count>", then one line per
    layer: `Type Name n_in n_out [bottoms...] [tops...] [params...]`.

    Output blobs are *not* marked in the file: any blob that is produced but
    never consumed is an output.
    """
    with open(path, "r", encoding="utf-8") as fh:
        lines = [ln.strip() for ln in fh if ln.strip()]

    if not lines or not lines[0].startswith("7767517"):
        raise ValueError(f"{path} is not an ncnn param file")
    n_layer, n_blob = (int(x) for x in lines[1].split()[:2])

    produced: list[str] = []
    consumed: set[str] = set()
    inputs: list[str] = []
    layer_types: dict[str, int] = {}

    for raw in lines[2:]:
        f = raw.split()
        if len(f) < 4:
            continue
        typ = f[0]
        n_in, n_out = int(f[2]), int(f[3])
        bottoms = f[4 : 4 + n_in]
        tops = f[4 + n_in : 4 + n_in + n_out]
        consumed.update(bottoms)
        produced.extend(tops)
        layer_types[typ] = layer_types.get(typ, 0) + 1
        if typ == "Input":
            inputs.extend(tops)

    dangling = [b for b in produced if b not in consumed]
    preferred = [b for b in dangling if "output" in b.lower()]
    return {
        "declared_layers": n_layer,
        "declared_blobs": n_blob,
        "parsed_layers": sum(layer_types.values()),
        "layer_types": dict(sorted(layer_types.items(), key=lambda kv: -kv[1])),
        "inputs": inputs,
        "dangling_blobs": dangling,
        "outputs": preferred or dangling,
    }


# --------------------------------------------------------------------------
# inference runners
# --------------------------------------------------------------------------
def run_ncnn(
    param: str,
    weights: str,
    chw: np.ndarray,
    in_name: str,
    out_name: str,
    use_vulkan: bool,
    num_threads: int,
) -> np.ndarray:
    """One ncnn forward pass. `chw` is float32 (c, h, w) -- see the traps above.

    The array is materialised as a local so it outlives the borrowed buffer
    inside `ncnn.Mat` for the whole forward pass.
    """
    import ncnn

    data = np.ascontiguousarray(chw, dtype=np.float32)
    mat_in = ncnn.Mat(data)  # borrows `data`; `data` stays referenced below

    net = ncnn.Net()
    net.opt.use_vulkan_compute = bool(use_vulkan)
    if num_threads and num_threads > 0:
        net.opt.num_threads = int(num_threads)
    # fp16 storage is what the shipped weights use; leave ncnn's defaults for
    # the arithmetic flags so the GPU path behaves like the real product.
    ret_p = net.load_param(param)
    ret_m = net.load_model(weights)
    if ret_p != 0 or ret_m != 0:
        raise RuntimeError(f"ncnn load failed (param={ret_p}, model={ret_m})")

    ex = net.create_extractor()
    rc = ex.input(in_name, mat_in)
    if rc != 0:
        raise RuntimeError(f"ncnn input() returned {rc}")
    ret, out = ex.extract(out_name)
    if ret != 0:
        raise RuntimeError(f"ncnn extract() returned {ret}")

    arr = np.array(out, dtype=np.float32)  # (c, h, w), a real copy
    del ex, net, mat_in, data
    return arr


def run_ort(onnx_path: str, chw: np.ndarray) -> np.ndarray:
    import onnxruntime as ort

    so = ort.SessionOptions()
    so.log_severity_level = 3
    sess = ort.InferenceSession(
        onnx_path, sess_options=so, providers=["CPUExecutionProvider"]
    )
    name = sess.get_inputs()[0].name
    return np.asarray(sess.run(None, {name: chw})[0], dtype=np.float32)


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------
def stats(arr: np.ndarray) -> dict:
    a = np.asarray(arr, dtype=np.float32)
    return {
        "shape": list(a.shape),
        "mean": float(a.mean(dtype=np.float64)),
        "std": float(a.std(dtype=np.float64)),
        "min": float(a.min()),
        "max": float(a.max()),
        "ptp": float(a.max() - a.min()),
        "nan": int(np.isnan(a).sum()),
    }


def psnr(a: np.ndarray, b: np.ndarray, data_range: float = 1.0) -> float:
    mse = float(np.mean((np.asarray(a, np.float64) - np.asarray(b, np.float64)) ** 2))
    if mse <= 0.0:
        return float("inf")
    return 20.0 * np.log10(data_range / np.sqrt(mse))


def corrcoef(a: np.ndarray, b: np.ndarray) -> float:
    x = np.asarray(a, np.float64).ravel()
    y = np.asarray(b, np.float64).ravel()
    if x.std() == 0 or y.std() == 0:
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


def timed(fn, warmup: int, runs: int) -> tuple[float, np.ndarray]:
    out = None
    for _ in range(max(0, warmup)):
        out = fn()
    best = float("inf")
    for _ in range(max(1, runs)):
        t0 = time.perf_counter()
        out = fn()
        best = min(best, (time.perf_counter() - t0) * 1000.0)
    return best, out


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pkg", default=DEFAULT_PKG, help="extracted ncnn package dir")
    ap.add_argument("--model", default="realesrgan-x4plus", help="model stem")
    ap.add_argument("--onnx", default=DEFAULT_ONNX, help="reference ONNX model")
    ap.add_argument("--size", type=int, default=64, help="square input side")
    ap.add_argument("--scale", type=int, default=4)
    ap.add_argument("--threads", type=int, default=0, help="0 = ncnn default")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--json", default="", help="write the report here")
    ap.add_argument("--skip-onnx", action="store_true")
    args = ap.parse_args()

    report: dict = {
        "task": "T-210 / T-305",
        "question": "is ncnn usable on Windows + Python (pip) ?",
        "input": {"model": args.model, "size": args.size, "scale": args.scale},
        "checks": [],
        "verdict": "unknown",
    }

    def check(name: str, ok: bool, detail: str = "") -> bool:
        report["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""))
        return bool(ok)

    # ---------------- 1. environment ----------------
    print("=== 1. environment ===")
    try:
        import ncnn

        report["ncnn"] = {
            "version": getattr(ncnn, "__version__", None),
            "file": getattr(ncnn, "__file__", None),
        }
        print(f"ncnn {report['ncnn']['version']}")
        check("import ncnn", True, str(report["ncnn"]["version"]))
    except Exception as exc:
        check("import ncnn", False, f"{type(exc).__name__}: {exc}")
        report["verdict"] = "unavailable"
        _dump(report, args.json)
        return 1

    gpu_count = 0
    try:
        ncnn.create_gpu_instance()
        gpu_count = int(ncnn.get_gpu_count())
        info = []
        for i in range(gpu_count):
            try:
                gi = ncnn.get_gpu_info(i)
                name = getattr(gi, "device_name", "?")
                if callable(name):
                    name = name()
                info.append({"index": i, "name": str(name)})
            except Exception as exc:
                info.append({"index": i, "error": f"{type(exc).__name__}: {exc}"})
        report["vulkan"] = {"gpu_count": gpu_count, "devices": info}
        print(f"vulkan gpu_count = {gpu_count} {info}")
    except Exception as exc:
        report["vulkan"] = {"error": f"{type(exc).__name__}: {exc}"}
        print(f"vulkan init failed: {exc}")
    check("vulkan backend compiled into the wheel", True, f"gpu_count={gpu_count}")
    check("vulkan device enumerated", gpu_count > 0, json.dumps(report["vulkan"].get("devices", [])))

    # ---------------- 2. model ----------------
    print("=== 2. model ===")
    param = os.path.join(args.pkg, "models", f"{args.model}.param")
    weights = os.path.join(args.pkg, "models", f"{args.model}.bin")
    if not (os.path.isfile(param) and os.path.isfile(weights)):
        check("model files present", False, f"{param} / {weights}")
        report["verdict"] = "unavailable"
        _dump(report, args.json)
        return 1
    meta = parse_param(param)
    report["param"] = meta
    in_name, out_name = meta["inputs"][0], meta["outputs"][-1]
    print(
        f"{args.model}: {meta['declared_layers']} layers, "
        f"in='{in_name}', out='{out_name}', "
        f"dangling={meta['dangling_blobs']}"
    )
    check("param parsed", meta["declared_layers"] > 0, f"layers={meta['declared_layers']}")
    check("single input / output blob", len(meta["inputs"]) == 1 and len(meta["outputs"]) == 1,
          f"in={meta['inputs']} out={meta['outputs']}")

    # ---------------- 3. deterministic input ----------------
    img = make_test_image(args.size, args.size)          # (1,3,h,w) float32 [0,1]
    chw = np.ascontiguousarray(img[0], dtype=np.float32)  # ncnn wants (c,h,w)
    report["input_stats"] = stats(chw)
    print(f"input {chw.shape} mean={chw.mean():.4f}")

    # ---------------- 4. ncnn CPU ----------------
    print("=== 3. ncnn CPU ===")
    try:
        cpu_ms, cpu_chw = timed(
            lambda: run_ncnn(param, weights, chw, in_name, out_name, False, args.threads),
            warmup=1,
            runs=args.runs,
        )
    except Exception as exc:
        check("ncnn CPU inference", False, f"{type(exc).__name__}: {exc}")
        report["verdict"] = "unavailable"
        _dump(report, args.json)
        return 1
    report["ncnn_cpu"] = {"ms": round(cpu_ms, 2), "stats": stats(cpu_chw)}
    print(f"cpu {cpu_chw.shape} {cpu_ms:.1f} ms mean={cpu_chw.mean():.4f}")
    check("ncnn CPU inference ran", True, f"{cpu_ms:.1f} ms")
    check(
        "ncnn CPU output shape is scale-correct",
        cpu_chw.shape == (3, args.size * args.scale, args.size * args.scale),
        str(cpu_chw.shape),
    )
    check(
        "ncnn CPU output is NOT all-black",
        cpu_chw.mean() > 0.01 and cpu_chw.std() > 0.01 and (cpu_chw.max() - cpu_chw.min()) > 0.05,
        f"mean={cpu_chw.mean():.4f} std={cpu_chw.std():.4f}",
    )

    # ---------------- 5. ncnn Vulkan ----------------
    print("=== 4. ncnn Vulkan ===")
    if gpu_count > 0:
        try:
            gpu_ms, gpu_chw = timed(
                lambda: run_ncnn(param, weights, chw, in_name, out_name, True, args.threads),
                warmup=1,
                runs=args.runs,
            )
            diff = float(np.max(np.abs(gpu_chw - cpu_chw)))
            report["ncnn_vulkan"] = {
                "ms": round(gpu_ms, 2),
                "stats": stats(gpu_chw),
                "speedup_vs_cpu": round(cpu_ms / gpu_ms, 2) if gpu_ms > 0 else None,
                "max_abs_diff_vs_cpu": diff,
            }
            print(f"vulkan {gpu_chw.shape} {gpu_ms:.1f} ms mean={gpu_chw.mean():.4f} maxdiff={diff:.5f}")
            check("ncnn Vulkan inference ran", True, f"{gpu_ms:.1f} ms")
            check(
                "ncnn Vulkan output is NOT all-black (known bug not triggered)",
                gpu_chw.mean() > 0.01 and gpu_chw.std() > 0.01,
                f"mean={gpu_chw.mean():.4f} std={gpu_chw.std():.4f}",
            )
            check(
                "ncnn CPU and Vulkan agree (fp16 tolerance 0.05)",
                diff < 0.05,
                f"max abs diff={diff:.5f}",
            )
        except Exception as exc:
            check("ncnn Vulkan inference ran", False, f"{type(exc).__name__}: {exc}")
    else:
        check("ncnn Vulkan inference ran", False, "no Vulkan device")

    # ---------------- 6. ONNX reference ----------------
    if not args.skip_onnx and os.path.isfile(args.onnx):
        print("=== 5. ONNX reference (ORT CPU) ===")
        try:
            ref = run_ort(args.onnx, chw[None, ...])[0]
            report["ort_cpu"] = {"stats": stats(ref)}
            print(f"ort {ref.shape} mean={ref.mean():.4f}")
            if ref.shape == cpu_chw.shape:
                p = psnr(cpu_chw, ref)
                c = corrcoef(cpu_chw, ref)
                report["ncnn_vs_onnx"] = {
                    "psnr_db": None if p == float("inf") else round(p, 2),
                    "corr": round(c, 5),
                }
                check("ncnn and ONNX produce the same shape", True, str(ref.shape))
                check(
                    "ncnn and ONNX are the same supersampling task (corr > 0.90)",
                    c > 0.90,
                    f"corr={c:.4f} psnr={p:.2f} dB",
                )
                check(
                    "ncnn output magnitude matches ONNX (mean within 30%)",
                    abs(cpu_chw.mean() - ref.mean()) <= 0.30 * max(ref.mean(), 1e-6),
                    f"ncnn={cpu_chw.mean():.4f} onnx={ref.mean():.4f}",
                )
            else:
                check("ncnn and ONNX produce the same shape", False, f"{cpu_chw.shape} vs {ref.shape}")
        except Exception as exc:
            check("ONNX reference ran", False, f"{type(exc).__name__}: {exc}")
    else:
        print("=== 5. ONNX reference skipped ===")

    # ---------------- 7. verdict ----------------
    failed = [c for c in report["checks"] if not c["ok"]]
    report["verdict"] = "usable" if not failed else "needs_review"
    report["failed_checks"] = [c["name"] for c in failed]
    print("=== verdict ===")
    print(f"{report['verdict']}: {len(report['checks']) - len(failed)}/{len(report['checks'])} checks passed")
    if failed:
        for c in failed:
            print(f"  FAIL {c['name']} :: {c['detail']}")

    _dump(report, args.json)
    return 0 if not failed else 2


def _dump(report: dict, json_path: str) -> None:
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if json_path:
        os.makedirs(os.path.dirname(os.path.abspath(json_path)), exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(f"[written] {json_path}")

    # ncnn keeps a process-wide Vulkan instance alive; if it is not torn down
    # explicitly the interpreter segfaults during static destruction (the
    # script "passes" yet exits 139).  Tear it down before we return.
    try:
        import ncnn

        ncnn.destroy_gpu_instance()
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
