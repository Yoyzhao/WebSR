"""Make execution-provider DLLs discoverable on Windows.

Two traps this solves, both hit and confirmed in this project:

1. ORT's CUDA EP needs cudart / cublas / cublasLt / cudnn. When you get them
   from the `nvidia-*-cuXX` pip packages they land in
   `site-packages/nvidia/<lib>/bin`, which is NOT on the DLL search path.
   Note the CUDA major version must match what ORT was built against
   (ORT 1.30 -> CUDA 13 + cuDNN 9; ORT <=1.22 -> CUDA 12).

2. ORT's OpenVINO EP needs `openvino.dll` from `site-packages/openvino/libs`.
   Since Python 3.8 the loader no longer searches PATH for extension-module
   dependencies, so putting that folder on PATH is not enough -- if the EP
   cannot load, ORT emits one warning and SILENTLY falls back to the CPU EP.

Always call `prepare()` before creating any InferenceSession. Verify afterwards
with `session.get_providers()` (which lists only providers that were actually
created) and, better, with an ORT profile that shows node-level assignment.
"""

from __future__ import annotations

import os
import site
import sys
import sysconfig


def _candidate_dirs() -> list[str]:
    dirs: list[str] = []

    try:
        purelib = sysconfig.get_paths()["purelib"]
    except Exception:
        purelib = ""

    if purelib:
        # CUDA / cuDNN / cuFFT / cuRAND from the nvidia-*-cuXX wheels
        nvidia_root = os.path.join(purelib, "nvidia")
        if os.path.isdir(nvidia_root):
            for pkg in sorted(os.listdir(nvidia_root)):
                for sub in ("bin", "lib"):
                    d = os.path.join(nvidia_root, pkg, sub)
                    if os.path.isdir(d):
                        dirs.append(d)

        # OpenVINO runtime
        for sub in ("libs", ""):
            d = os.path.join(purelib, "openvino", sub)
            if os.path.isdir(d) and any(
                f.lower().startswith("openvino") for f in os.listdir(d)
            ):
                dirs.append(d)
                break

    # user site-packages (some setups install OpenVINO there)
    try:
        for us in site.getusersitepackages().split(os.pathsep):
            d = os.path.join(us, "openvino", "libs")
            if os.path.isdir(d):
                dirs.append(d)
    except Exception:
        pass

    # legacy: environment variables
    for key in ("CUDA_PATH", "OPENVINO_RUNTIME_DIR"):
        v = os.environ.get(key)
        if v and os.path.isdir(v):
            dirs.append(v)
            dirs.append(os.path.join(v, "bin"))

    return dirs


def prepare(verbose: bool = False) -> dict:
    """Register every candidate directory for DLL resolution. Returns a report."""
    report = {"added": [], "failed": [], "path_prepended": []}

    dirs = _candidate_dirs()
    for d in dirs:
        try:
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(d)
            report["added"].append(d)
        except Exception as exc:
            report["failed"].append(f"{d} :: {type(exc).__name__}: {exc}")

    # PATH is still consulted by some native loaders and by subprocesses
    if dirs:
        os.environ["PATH"] = os.pathsep.join(dirs + [os.environ.get("PATH", "")])
        report["path_prepended"] = dirs

    if verbose:
        for d in report["added"]:
            print(f"[dll-path] + {d}")
        for f in report["failed"]:
            print(f"[dll-path] ! {f}")

    return report


def preload_ort_dlls(verbose: bool = False) -> bool:
    """Call onnxruntime.preload_dlls() if the installed ORT provides it.

    ORT 1.21+ ships this helper precisely because the manual DLL dance is
    error-prone. It is a no-op on non-Windows platforms.
    """
    if sys.platform != "win32":
        return False
    try:
        import onnxruntime as ort

        fn = getattr(ort, "preload_dlls", None)
        if fn is None:
            return False
        try:
            fn()
        except TypeError:
            # older signature: preload_dlls(directory="")
            fn("")
        if verbose:
            print("[dll-path] onnxruntime.preload_dlls() ok")
        return True
    except Exception as exc:
        if verbose:
            print(f"[dll-path] preload_dlls failed: {type(exc).__name__}: {exc}")
        return False


def main() -> int:
    import onnxruntime as ort

    print("=== before ===")
    print("available:", ort.get_available_providers())
    report = prepare(verbose=True)
    preload_ort_dlls(verbose=True)
    print("=== after ===")
    print("available:", ort.get_available_providers())
    print("dirs registered:", len(report["added"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
