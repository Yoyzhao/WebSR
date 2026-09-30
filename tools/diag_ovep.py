"""Diagnose why onnxruntime's OpenVINO EP silently falls back to CPU.

Run inside .venvs/sr-ov:
    python tools/diag_ovep.py

It walks the load chain step by step so the failure point is unambiguous:
  1. is openvino.dll loadable at all, and what version does it report?
  2. is onnxruntime_providers_openvino.dll loadable once (1) is on the
     DLL search path?  A WinError 127 here = ABI mismatch (missing export),
     NOT a missing file.
  3. what does ORT itself say when it tries to create the EP?  Captured with
     a verbose log sink, because ORT swallows EP-creation failures into a
     single warning and then quietly runs everything on CPU.
"""

from __future__ import annotations

import ctypes
import os
import sys
import sysconfig

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _runtime_env import prepare  # noqa: E402


def rule(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def main() -> int:
    purelib = sysconfig.get_paths()["purelib"]
    ov_libs = os.path.join(purelib, "openvino", "libs")
    ov_root = os.path.join(purelib, "openvino")
    ov_dir = ov_libs if os.path.isdir(ov_libs) else ov_root

    rule("0. DLL search path")
    report = prepare(verbose=True)
    print(f"purelib   : {purelib}")
    print(f"openvino  : {ov_dir}  (exists={os.path.isdir(ov_dir)})")
    print(f"registered: {len(report['added'])} dirs")

    rule("1. load openvino.dll directly")
    ov_dll_path = os.path.join(ov_dir, "openvino.dll")
    print(f"path      : {ov_dll_path}  (exists={os.path.isfile(ov_dll_path)})")
    ov_handle = None
    if os.path.isfile(ov_dll_path):
        try:
            ov_handle = ctypes.WinDLL(ov_dll_path)
            print("WinDLL    : OK")
        except OSError as exc:
            print(f"WinDLL    : FAILED  {type(exc).__name__}: {exc}")

    if ov_handle is not None:
        try:
            ov_handle.ov_get_serialization_version.argtypes = [ctypes.c_char_p]
            buf = ctypes.create_string_buffer(256)
            ok = ov_handle.ov_get_serialization_version(buf)
            print(f"OV serialization version: {buf.value.decode(errors='replace')}")
        except Exception as exc:
            print(f"ov_get_serialization_version: could not call ({type(exc).__name__}: {exc})")

    rule("2. load onnxruntime_providers_openvino.dll")
    ep_dll = os.path.join(purelib, "onnxruntime", "capi", "onnxruntime_providers_openvino.dll")
    print(f"path      : {ep_dll}  (exists={os.path.isfile(ep_dll)})")
    if os.path.isfile(ep_dll):
        try:
            ctypes.WinDLL(ep_dll)
            print("WinDLL    : OK")
        except OSError as exc:
            print(f"WinDLL    : FAILED  errno={exc.errno}  {exc}")
            print("            NOTE: this standalone ctypes probe is NOT authoritative.")
            print("            A provider DLL is loaded by onnxruntime.dll inside a host")
            print("            process; loading it bare can fail with 1114 even when ORT")
            print("            itself loads it fine. Judge success in step 4 instead.")
            print("            errno 127 = a dependency is missing an expected export")
            print("                        (a genuine ABI mismatch).")
            print("            errno 1114 = DllMain failed; usually just the probe.")

    rule("3. what do each of the two wheels say they are")
    for pkg in ("openvino", "onnxruntime-openvino"):
        try:
            import importlib.metadata as md

            print(f"{pkg:24s} {md.distribution(pkg).version}")
        except Exception as exc:
            print(f"{pkg:24s} ?? ({exc})")
    print("\nOn Windows the ORT OpenVINO EP is built against ONE OpenVINO release and")
    print("hard-links openvino.dll. The build path is embedded in the EP DLL, e.g.")
    print("  C:\\Users\\...\\openvino_toolkit_windows_2025.4.1.20426.82bbf0292c5_x86_64")
    print("so onnxruntime-openvino 1.24.1 needs openvino 2025.4.1 and NOT a newer one.")

    rule("4. let ORT try, with a verbose log sink")
    import onnxruntime as ort

    print("available providers before:", ort.get_available_providers())
    so = ort.SessionOptions()
    so.log_severity_level = 0  # verbose
    model = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "models", "RealESRGAN_x4.onnx",
    )
    try:
        sess = ort.InferenceSession(
            model, sess_options=so, providers=["OpenVINOExecutionProvider", "CPUExecutionProvider"]
        )
        print("session providers        :", sess.get_providers())
    except Exception as exc:
        print(f"session creation raised  : {type(exc).__name__}: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
