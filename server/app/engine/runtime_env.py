"""Windows 推理运行时 DLL 路径注册（tech-arch §6.6 启动序列第 1 步）。

本模块是 `tools/_runtime_env.py` 逻辑的产品化重写 —— 产品代码**不得** import
`tools/`（tech-arch §6.7）。两个已被实测确认的陷阱：

1. ORT CUDA EP 需要的 cudart / cublas / cublasLt / cudnn 由 `nvidia-*-cuXX`
   pip 包提供，落在 `site-packages/nvidia/<lib>/bin`，不在 DLL 搜索路径上；
2. ORT OpenVINO EP 需要 `site-packages/openvino/libs/openvino.dll`。Python 3.8
   起扩展模块依赖不再搜 PATH —— EP 加载失败时 ORT 只打一条 warning 就
   **静默回退 CPU**（project-rules §2.2 铁律 2 的成因之一）。
3. **CUDA 13 起 `nvidia-*` wheel 多了一层架构目录**：CUDA 12 时代 DLL 直接在
   `nvidia/<pkg>/bin/`，CUDA 13 改成 `nvidia/cu13/bin/x86_64/*.dll`（cuDNN 仍是
   `nvidia/cudnn/bin/`）。漏掉这一层 → cudart / cublasLt 找不到，ORT 报
   `Error loading onnxruntime_providers_cuda.dll which depends on cublasLt64_13.dll
   which is missing` 后**静默回退 CPU**（2026-10-09 实测捕获）。

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


def _arch_dll_dirs(parent: str) -> list[str]:
    """把 `bin/` 下再套的一层架构目录（x86_64 / amd64）也纳进来。

    CUDA 12 的 `nvidia-*-cu12` wheel 把 DLL 直接放 `bin/`；**CUDA 13 起**
    （`nvidia-cuda-runtime` / `nvidia-cublas` / `nvidia-cufft` 等）改成
    `bin/x86_64/*.dll`。只注册外层会得到一个**不含 DLL 的空目录**，
    最终表现为 ORT 静默回退 CPU（见模块 docstring 第 3 条）。

    只返回**确实含 `.dll` 的**子目录，避免把 `include/` 之类也塞进搜索路径。
    """
    out: list[str] = []
    try:
        entries = sorted(os.listdir(parent))
    except OSError:
        return out
    for name in entries:
        sub = os.path.join(parent, name)
        try:
            if os.path.isdir(sub) and any(
                f.lower().endswith(".dll") for f in os.listdir(sub)
            ):
                out.append(sub)
        except OSError:
            continue
    return out


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
                        # CUDA 13 起 DLL 在 bin/<arch>/ 而非 bin/，见 _arch_dll_dirs
                        dirs.extend(_arch_dll_dirs(d))

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
