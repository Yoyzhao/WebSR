"""T-806 跨环境探针：在**别的 venv** 里用产品加载器跑一次真实推理。

存在理由：应用环境 `.venvs/sr-app` **只装了 CPU 版 onnxruntime**，没有 openvino / ncnn
（可选后端的运行时安装属部署决定）。若只在 sr-app 里验证，"四格式可加载"就只证明了
两条分支会正确地报 `runtime_missing`，没有证明 IR / ncnn 的加载代码**真的能跑**。

而 `engine/` 层不依赖 fastapi / sqlalchemy / pydantic（引擎纯度），所以这里可以直接
把**产品代码本身**拿到 `.venvs/sr-ov` / `.venvs/sr-ncnn` 里执行——
验证的是产品实现，不是一份"测试专用"的平行实现。

用法（由 `verify_t806_loader.py` 以子进程调用）:

    .venvs/sr-ov/Scripts/python.exe scripts/test-script/_t806_backend_probe.py \
        --fmt openvino_ir --path data/models/ir/RealESRGAN_x4_fp16.xml \
        --companion data/models/ir/RealESRGAN_x4_fp16.bin --size 64 --scale 4 --json out.json
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))

# 注意：这里**不**在顶层 import `pipeline`——它依赖 Pillow，而 `.venvs/sr-ncnn`
# 只装了 numpy（ncnn 的 opencv 运行期并不加载）。需要全链路时才惰性导入。
from app.engine import model_loader  # noqa: E402

_RUNTIME_OF = {"openvino_ir": "openvino", "ncnn": "ncnn", "onnx": "onnxruntime"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fmt", required=True)
    ap.add_argument("--path", required=True)
    ap.add_argument("--companion", default="")
    ap.add_argument("--size", type=int, default=64)
    ap.add_argument("--scale", type=int, default=4)
    ap.add_argument("--tile", type=int, default=0, help=">0 时改用 pipeline 全链路")
    ap.add_argument("--overlap", type=int, default=0)
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    report: dict = {"fmt": args.fmt, "ok": False}

    spec = model_loader.LoadSpec(
        path=Path(args.path),
        fmt=args.fmt,
        companion=Path(args.companion) if args.companion else None,
        scale=args.scale,
        backend="CPUExecutionProvider",
    )
    try:
        backend = model_loader.load_backend(spec)
    except model_loader.ModelLoadError as exc:
        report["error"] = exc.to_dict()
        _dump(report, args.json)
        return 2

    try:
        report["backend"] = backend.describe()
        report["runtime"] = importlib.metadata.version(_RUNTIME_OF[args.fmt])

        if args.tile > 0:
            # 全链路：预处理 → 分块 → 逐块推理 → 羽化拼接 → 落盘
            import numpy as np
            from PIL import Image

            from app.engine import pipeline
            from app.engine.image_ops import PreprocessSpec

            tmp = ROOT / ".workbuddy" / "verify" / "t806" / "probe"
            tmp.mkdir(parents=True, exist_ok=True)
            src = tmp / f"probe_{args.fmt}_in.png"
            arr = np.zeros((args.size, args.size, 3), dtype=np.uint8)
            arr[..., 0] = np.linspace(0, 255, args.size)[None, :].astype(np.uint8)
            arr[..., 1] = np.linspace(0, 255, args.size)[:, None].astype(np.uint8)
            arr[..., 2] = 90
            Image.fromarray(arr).save(src)

            out = tmp / f"probe_{args.fmt}_out.png"
            res = pipeline.run_upscale(backend, pipeline.UpscaleRequest(
                source_path=src, output_path=out,
                tile=args.tile, overlap=args.overlap, feather_px=args.overlap,
                scale=args.scale, spec=PreprocessSpec(),
            ))
            report["outcome"] = res.to_dict()
            report["outcome"]["output_path"] = str(out)
        else:
            # 单块直接推理（sr-ncnn 无 Pillow，走这条）
            import numpy as np

            x = np.zeros((1, 3, args.size, args.size), dtype=np.float32)
            x[0, 0] = np.linspace(0, 1, args.size)[None, :]
            x[0, 1] = np.linspace(0, 1, args.size)[:, None]
            x[0, 2] = 0.35
            y = np.asarray(backend.infer(x), dtype=np.float32)
            report["shape"] = list(y.shape)
            report["stats"] = {
                "mean": float(y.mean()), "std": float(y.std()),
                "min": float(y.min()), "max": float(y.max()),
                "nan": int(np.isnan(y).sum()),
            }
        report["ok"] = True
    except Exception as exc:  # noqa: BLE001
        report["error"] = {"code": "probe_failed", "message": f"{type(exc).__name__}: {exc}"}
    finally:
        backend.close()

    _dump(report, args.json)
    return 0 if report["ok"] else 3


def _dump(report: dict, json_path: str) -> None:
    print(json.dumps(report, ensure_ascii=False))
    if json_path:
        p = Path(json_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
