"""Windows 推理运行时 DLL 路径注册（tech-arch §6.6 启动序列第 1 步）。

本模块是 `tools/_runtime_env.py` 逻辑的产品化重写 —— 产品代码**不得** import
`tools/`（tech-arch §6.7）。两个已被实测确认的陷阱：

1. ORT CUDA EP 需要的 cudart / cublas / cublasLt / cudnn 由 `nvidia-*-cuXX`
   pip 包提供，落在 `site-packages/nvidia/<lib>/bin`，不在 DLL 搜索路径上；
2. ORT OpenVINO EP 需要 `site-packages/openvino/libs/openvino.dll`。Python 3.8
   起扩展模块依赖不再搜 PATH —— EP 加载失败时 ORT 只打一条 warning 就
   **静默回退 CPU**（project-rules §2.2 铁律 2 的成因之一）。

**必须在 import onnxruntime / openvino 之前调用**（本模块自身不 import 任何推理库）。
失败处理：注册失败不阻断启动，由调用方降级为仅 CPU 可用（tech-arch §6.6）。

注：`onnxruntime.preload_dlls()`（ORT 1.21+ 官方辅助）随 T-803 引入推理依赖后
在引擎初始化处调用，不属于本启动步。
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
        # CUDA / cuDNN / cuFFT / cuRAND（nvidia-*-cuXX wheels）
        nvidia_root = os.path.join(purelib, "nvidia")
        if os.path.isdir(nvidia_root):
            for pkg in sorted(os.listdir(nvidia_root)):
                for sub in ("bin", "lib"):
                    d = os.path.join(nvidia_root, pkg, sub)
                    if os.path.isdir(d):
                        dirs.append(d)

        # OpenVINO 运行时
        for sub in ("libs", ""):
            d = os.path.join(purelib, "openvino", sub)
            if os.path.isdir(d) and any(
                f.lower().startswith("openvino") for f in os.listdir(d)
            ):
                dirs.append(d)
                break

    # user site-packages（部分环境 OpenVINO 装在用户目录）
    try:
        for us in site.getusersitepackages().split(os.pathsep):
            d = os.path.join(us, "openvino", "libs")
            if os.path.isdir(d):
                dirs.append(d)
    except Exception:
        pass

    # 传统安装：环境变量
    for key in ("CUDA_PATH", "OPENVINO_RUNTIME_DIR"):
        v = os.environ.get(key)
        if v and os.path.isdir(v):
            dirs.append(v)
            dirs.append(os.path.join(v, "bin"))

    return dirs


def prepare_dll_paths() -> dict:
    """注册全部候选目录到 DLL 搜索路径。返回报告 {added, failed, path_prepended}。

    非 Windows 平台直接返回空报告（no-op）。
    """
    report: dict = {"added": [], "failed": [], "path_prepended": []}
    if sys.platform != "win32":
        return report

    dirs = _candidate_dirs()
    for d in dirs:
        try:
            os.add_dll_directory(d)  # type: ignore[attr-defined]  # Windows only
            report["added"].append(d)
        except Exception as exc:  # 单目录失败不阻断整体（降级由调用方决策）
            report["failed"].append(f"{d} :: {type(exc).__name__}: {exc}")

    # 部分原生加载器与子进程仍查 PATH
    if dirs:
        os.environ["PATH"] = os.pathsep.join(dirs + [os.environ.get("PATH", "")])
        report["path_prepended"] = dirs

    return report
