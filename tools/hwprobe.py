#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hwprobe.py —— 超分/修复应用的硬件探测与 Profile 推荐

用途：
  1. 探测本机 CPU / 内存 / GPU（NVIDIA / Intel / AMD）/ 图形运行库
  2. 根据探测结果推荐可用的推理后端、模型档位、精度、tile 参数
  3. 输出 JSON，可直接被应用的能力层消费

设计原则：
  - 只读探测，不做任何修改
  - 任何一项探测失败都不阻断整体流程（全部 try/except 包裹）
  - 输出的 profile 里带上"降级链"，应用据此做自动降级

用法：
  python hwprobe.py            # 人类可读输出
  python hwprobe.py --json     # 机器可读 JSON
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field, asdict

IS_WINDOWS = sys.platform == "win32"


# --------------------------------------------------------------------------
# 底层工具
# --------------------------------------------------------------------------
def _run(cmd: list[str], timeout: int = 15) -> str:
    """执行命令并返回 stdout；失败返回空串，绝不抛异常。"""
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        raw = p.stdout or b""
        return raw.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def _ps(command: str, timeout: int = 20) -> str:
    """执行 PowerShell 命令（仅用于 Windows 的 WMI 查询）。"""
    if not IS_WINDOWS:
        return ""
    return _run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
        ],
        timeout=timeout,
    )


def _to_int(v: str) -> int:
    try:
        return int(float(str(v).strip()))
    except Exception:
        return 0


# --------------------------------------------------------------------------
# 探测：CPU / 内存 / 系统
# --------------------------------------------------------------------------
def probe_cpu() -> dict:
    info = {
        "name": platform.processor() or "unknown",
        "physical_cores": 0,
        "logical_cores": os.cpu_count() or 0,
        "has_igpu": None,  # None=未知，True/False=判定结果
        "note": "",
    }
    if IS_WINDOWS:
        raw = _ps("(Get-CimInstance Win32_Processor).Name")
        if raw:
            info["name"] = raw.splitlines()[0].strip()
        cores = _ps("(Get-CimInstance Win32_Processor).NumberOfCores")
        if cores:
            info["physical_cores"] = _to_int(cores.splitlines()[0])

    # Intel 桌面 CPU 的 F / KF 后缀表示屏蔽了核显
    name_upper = info["name"].upper()
    if "INTEL" in name_upper or "CORE" in name_upper:
        suffix_f = any(
            tok.endswith("F") and not tok.endswith("KF") is False
            for tok in name_upper.replace("(R)", " ").replace("(TM)", " ").split()
        )
        # 更稳妥的写法：直接判断型号尾部
        tail = name_upper.replace("(R)", "").replace("(TM)", "").strip().split()[-1]
        if tail.endswith("F") or tail.endswith("KF"):
            info["has_igpu"] = False
            info["note"] = "Intel F/KF 后缀型号已屏蔽核显，无法走 Intel 集显路径"
        else:
            info["has_igpu"] = True if "INTEL" in name_upper else None
        del suffix_f
    return info


def probe_memory_gb() -> float:
    if IS_WINDOWS:
        raw = _ps(
            "[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB,1)"
        )
        if raw:
            try:
                return float(raw.splitlines()[0].strip())
            except Exception:
                pass
    try:
        import psutil  # type: ignore

        return round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except Exception:
        return 0.0


# --------------------------------------------------------------------------
# 探测：显卡
# --------------------------------------------------------------------------
def probe_nvidia() -> list[dict]:
    """通过 nvidia-smi 获取 NVIDIA 显卡（型号 / 显存 / 驱动 / 算力）。"""
    gpus: list[dict] = []
    if not shutil.which("nvidia-smi"):
        return gpus
    raw = _run(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version,compute_cap",
            "--format=csv,noheader,nounits",
        ]
    )
    for line in raw.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        gpus.append(
            {
                "vendor": "nvidia",
                "name": parts[0],
                "vram_mb": _to_int(parts[1]),
                "driver": parts[2],
                "compute_cap": parts[3],
            }
        )
    return gpus


def probe_all_adapters() -> list[dict]:
    """枚举所有显示适配器（含 Intel / AMD 集显与独显），用于识别非 N 卡。"""
    adapters: list[dict] = []
    if not IS_WINDOWS:
        return adapters
    raw = _ps(
        "Get-CimInstance Win32_VideoController | "
        "ForEach-Object { $_.Name + '|' + $_.DriverVersion + '|' + $_.AdapterCompatibility }"
    )
    for line in raw.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2 or not parts[0]:
            continue
        name = parts[0]
        low = name.lower()
        if "nvidia" in low:
            vendor = "nvidia"
        elif "intel" in low:
            vendor = "intel"
        elif "amd" in low or "radeon" in low:
            vendor = "amd"
        elif "virtual" in low or "gameviewer" in low or "basic display" in low:
            vendor = "virtual"
        else:
            vendor = "other"
        adapters.append(
            {
                "vendor": vendor,
                "name": name,
                "driver": parts[1] if len(parts) > 1 else "",
                "vendor_string": parts[2] if len(parts) > 2 else "",
            }
        )
    return adapters


# --------------------------------------------------------------------------
# 探测：图形 / 推理运行库
# --------------------------------------------------------------------------
def probe_runtimes() -> dict:
    rt: dict = {
        "vulkan": False,
        "directml": False,
        "cuda_toolkit": False,
        "tensorrt": False,
        "onnxruntime_providers": [],
        "torch": None,
    }
    if IS_WINDOWS:
        sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
        rt["vulkan"] = os.path.exists(os.path.join(sys32, "vulkan-1.dll"))
        rt["directml"] = os.path.exists(os.path.join(sys32, "DirectML.dll"))
    rt["cuda_toolkit"] = bool(os.environ.get("CUDA_PATH")) or bool(shutil.which("nvcc"))
    rt["tensorrt"] = bool(shutil.which("trtexec"))

    try:
        import onnxruntime as ort  # type: ignore

        rt["onnxruntime_providers"] = list(ort.get_available_providers())
        rt["onnxruntime_version"] = ort.__version__
    except Exception:
        rt["onnxruntime_version"] = None

    try:
        import torch  # type: ignore

        rt["torch"] = {
            "version": torch.__version__,
            "cuda_available": bool(torch.cuda.is_available()),
            "cuda_version": getattr(torch.version, "cuda", None),
        }
    except Exception:
        rt["torch"] = None

    # OpenVINO：Intel 平台（含集显）的最佳运行时
    #
    # ⚠️ 实测教训（2026-09-29，本机 i5-12400F + RTX 3050）：
    #   core.available_devices 返回 ['CPU', 'GPU']，其中 'GPU' 的
    #   FULL_DEVICE_NAME 竟然是 "NVIDIA GeForce RTX 3050 OEM (dGPU)"。
    #   即 OpenVINO 2026.x 的 GPU 插件会通过 OpenCL 枚举并"支持"非 Intel 显卡。
    #   但它在这类设备上又慢又脆（tile512 30.3s，比同机 CPU 的 23.7s 还慢；
    #   tile1024 直接抛 "Exceeded max size of memory object allocation"）。
    #   所以：**不能只看 "GPU" 在不在 devices 里**，必须读 FULL_DEVICE_NAME
    #   判断厂商，并且只把 Intel GPU 当作可用的加速路径。
    try:
        import openvino as ov  # type: ignore

        core = ov.Core()
        devices = list(core.available_devices)
        gpu_devices = []
        for d in devices:
            if not d.startswith("GPU"):
                continue
            try:
                full_name = str(core.get_property(d, "FULL_DEVICE_NAME"))
            except Exception:
                full_name = "unknown"
            gpu_devices.append({"device": d, "full_name": full_name})
        intel_gpus = [
            g for g in gpu_devices if any(k in g["full_name"] for k in ("Intel", "Arc"))
        ]
        vendor_prefixed = next((d for d in devices if d.startswith("NPU")), None)
        rt["openvino"] = {
            "installed": True,
            "version": getattr(ov, "__version__", "unknown"),
            "devices": devices,
            "gpu_devices": gpu_devices,
            "intel_gpus": intel_gpus,
            "has_intel_gpu": bool(intel_gpus),
            "has_non_intel_gpu": bool(gpu_devices) and not bool(intel_gpus),
            "has_npu": bool(vendor_prefixed),
        }
    except Exception:
        rt["openvino"] = {
            "installed": False,
            "version": None,
            "devices": [],
            "gpu_devices": [],
            "intel_gpus": [],
            "has_intel_gpu": False,
            "has_non_intel_gpu": False,
            "has_npu": False,
        }

    return rt


# --------------------------------------------------------------------------
# Profile 推荐
# --------------------------------------------------------------------------
def tile_for_vram(vram_mb: int) -> int:
    gb = vram_mb / 1024
    if gb >= 20:
        return 0
    if gb >= 11:
        return 768
    if gb >= 7.5:
        return 512
    if gb >= 5.5:
        return 384
    if gb >= 3.5:
        return 256
    return 128


@dataclass
class Profile:
    tier: str
    label: str
    backend: str
    precision: str
    tile: int
    tile_pad: int
    models: list[str] = field(default_factory=list)
    max_input_px: int = 0
    degradations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    expected: str = ""


def recommend(cpu: dict, nvidia: list[dict], adapters: list[dict], rt: dict, ram: float) -> Profile:
    """依据探测结果给出推荐 Profile。"""
    warnings: list[str] = []
    non_nvidia = [a for a in adapters if a["vendor"] in ("intel", "amd")]

    # ---- 情况 A：有 NVIDIA 独显 -------------------------------------------
    if nvidia:
        g = max(nvidia, key=lambda x: x["vram_mb"])
        vram = g["vram_mb"]
        gb = vram / 1024
        tile = tile_for_vram(vram)

        if gb >= 20:
            tier, label = "Tier 3", "专业/旗舰显卡"
            models = ["SUPIR v0Q", "HYPIR", "SeedVR2-7B", "RealESRGAN_x4plus"]
            backend = "TensorRT 或 ONNX Runtime CUDA"
            max_input = 0
            expected = "扩散类 10~60 秒/张；开启 batch 后吞吐优先"
        elif gb >= 15:
            tier, label = "Tier 2", "高端消费卡"
            models = ["OSEDiff", "InvSR", "DiffBIR v2.1", "SeedVR2-3B", "RealESRGAN_x4plus"]
            backend = "ONNX Runtime CUDA / TensorRT"
            max_input = 0
            expected = "扩散类 3~10 秒/张；Real-ESRGAN 亚秒级"
        else:
            tier, label = "Tier 1", "消费级显卡（8G 档）"
            models = [
                "RealESRGAN_x4plus",
                "realesr-general-x4v3",
                "RealESRGAN_x4plus_anime_6B",
                "CodeFormer / GFPGAN(人脸)",
            ]
            backend = "ONNX Runtime CUDA（进阶可换 TensorRT）"
            max_input = 4096
            expected = "1080p→4K 秒级；扩散类不建议默认开启"

        if gb < 15:
            warnings.append(
                f"显存 {gb:.1f}GB 不足以稳定运行 SUPIR / SeedVR2-7B，"
                "请将扩散类模型作为可选下载包而非默认能力"
            )
        if gb < 15 and ram and ram < 32:
            warnings.append(
                f"系统内存仅 {ram:.0f}GB，跑扩散模型时 CPU 侧内存与显存会同时吃紧"
            )
        if not rt["cuda_toolkit"]:
            warnings.append(
                "未检测到 CUDA Toolkit —— 这【不是问题】："
                "onnxruntime-gpu / PyTorch 的 pip wheel 自带 CUDA 运行时，无需单独安装 Toolkit"
            )
        if not any("CUDAExecutionProvider" in p for p in rt["onnxruntime_providers"]):
            warnings.append(
                "当前 onnxruntime 为 CPU 版：需 `pip uninstall onnxruntime` 后安装 "
                "`onnxruntime-gpu` 才能启用 CUDA 加速"
            )

        return Profile(
            tier=tier,
            label=label,
            backend=backend,
            precision="fp16",
            tile=tile,
            tile_pad=10,
            models=models,
            max_input_px=max_input,
            degradations=["tile 512→384→256→128", "换 general-x4v3 小模型", "提示不支持"],
            warnings=warnings,
            expected=expected,
        )

    # ---- 情况 B：只有 Intel / AMD 集显 -------------------------------------
    if non_nvidia:
        a = non_nvidia[0]
        igpu_name = a["name"]
        ov = rt.get("openvino") or {}
        ov_devices = ov.get("devices") or []
        # ★ 必须按厂商判断，不能只看 "GPU" 是否存在（见 probe_runtimes 里的实测教训）
        has_ov_gpu = bool(ov.get("has_intel_gpu"))
        non_intel_gpu = ov.get("has_non_intel_gpu")

        # Intel 平台优先级：OpenVINO > Vulkan(ncnn) > DirectML(不推荐) > CPU
        if has_ov_gpu:
            intel_gpu_names = ", ".join(
                g["full_name"] for g in (ov.get("intel_gpus") or [])
            )
            return Profile(
                tier="Tier 0-iGPU",
                label=f"集成显卡（OpenVINO GPU：{intel_gpu_names}）",
                backend="OpenVINO GPU",
                precision="FP16（OpenVINO 对 Intel GPU 有 FP16 优化）",
                tile=256,
                tile_pad=16,
                models=[
                    "Real-ESRGAN x4plus IR",
                    "Real-ESRGAN x4plus_anime_6B IR",
                    "realesr-general-x4v3 IR",
                ],
                max_input_px=1920,
                degradations=["tile 256→192→128", "降到 2x 放大", "切 OpenVINO CPU"],
                warnings=[
                    "Intel 集显与系统共享内存，16GB 以下机型处理大图易触发内存压力",
                    "务必用 OpenVINO IR（.xml/.bin）而非原始 ONNX，转换后性能差距可达数倍",
                ],
                expected=f"{igpu_name}：1080p 4x 约十几秒级，适合预览与轻度使用",
            )

        if ov.get("installed"):
            warnings = [
                f"OpenVINO 已安装（{ov.get('version')}），但可用设备为 {ov_devices}，"
                "其中没有 Intel GPU",
                "请确认已安装 Intel 显卡驱动，并安装 GPU 插件后重试",
            ]
            if non_intel_gpu:
                warnings.append(
                    "★ 检测到 OpenVINO 把非 Intel 显卡也枚举成了 GPU 设备"
                    f"（{ov.get('gpu_devices')}），但实测这条路径不可用："
                    "在 RTX 3050 上 tile512 需 30.3s（比同机 CPU 的 23.7s 还慢），"
                    "tile1024 直接抛显存分配异常 —— 不要据此判定为可加速"
                )
            return Profile(
                tier="Tier 0-iGPU",
                label="集成显卡（OpenVINO 已装但无 Intel GPU 设备）",
                backend="OpenVINO AUTO / CPU",
                precision="FP32（CPU 上禁用 FP16）",
                tile=192,
                tile_pad=16,
                models=["realesr-general-x4v3 IR", "Real-ESRGAN x2plus IR"],
                max_input_px=1600,
                degradations=["tile 192→128", "降到 2x", "切纯 CPU"],
                warnings=warnings,
                expected="1080p 4x 约数十秒级",
            )

        if rt["vulkan"]:
            return Profile(
                tier="Tier 0-iGPU",
                label="集成显卡（仅 Vulkan 可用）",
                backend="ncnn-Vulkan（建议补装 OpenVINO 以获得更好性能）",
                precision="FP32",
                tile=256,
                tile_pad=16,
                models=["realesr-general-x4v3 (ncnn)", "Real-CUGAN (ncnn)"],
                max_input_px=1920,
                degradations=["tile 256→192→128", "降到 2x", "切纯 CPU"],
                warnings=[
                    "ncnn-Vulkan 要求 Vulkan 1.0+ 与支持 compute shader 的驱动；"
                    "Upscayl 官方兼容表已确认 Intel HD Graphics 620 / Iris Graphics 可用，"
                    "但老驱动或过旧的 HD/UHD 仍可能失败，需实机确认",
                    "若失败，优先补装 OpenVINO（Intel 官方推荐路径，性能与兼容性都更好）",
                    "【不要选 DirectML】实测在 Intel 平台上比纯 CPU 还慢（约 2~3 倍慢）",
                ],
                expected=f"{igpu_name}：1080p 4x 约十几秒~分钟级，仅适合预览",
            )

        return Profile(
            tier="Tier 0-CPU",
            label="纯 CPU（集显不支持加速）",
            backend="OpenVINO CPU（推荐）或 ONNX Runtime CPU EP",
            precision="FP32 / INT8（CPU 上禁用 FP16）",
            tile=128,
            tile_pad=16,
            models=["realesr-general-x4v3 IR", "OpenCV FSRCNN/ESPCN（极轻预览）"],
            max_input_px=1280,
            degradations=["tile 128→96", "降到 2x", "提示当前硬件仅支持预览"],
            warnings=[
                "CPU 上不要启用 FP16",
                "【不要选 DirectML】在 Intel/纯 CPU 平台上比 CPU EP 更慢",
            ],
            expected="1080p 4x 约数十秒~分钟级",
        )

    # ---- 情况 C：无任何可用 GPU -------------------------------------------
    return Profile(
        tier="Tier 0-CPU",
        label="纯 CPU（无独显/无集显）",
        backend="ONNX Runtime CPU EP（或 ncnn CPU）",
        precision="fp32 / int8（CPU 上禁用 fp16）",
        tile=128,
        tile_pad=16,
        models=["realesr-general-x4v3", "OpenCV FSRCNN/ESPCN（极轻预览）"],
        max_input_px=1280,
        degradations=["tile 128→96", "降到 2x", "提示当前硬件仅支持预览"],
        warnings=[
            "本机无可用 GPU 加速设备",
            "CPU 上不要启用 fp16",
        ],
        expected="1080p 4x 约数十秒~分钟级",
    )


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def main() -> int:
    cpu = probe_cpu()
    ram = probe_memory_gb()
    nvidia = probe_nvidia()
    adapters = probe_all_adapters()
    rt = probe_runtimes()

    prof = recommend(cpu, nvidia, adapters, rt, ram)

    report = {
        "system": {
            "os": f"{platform.system()} {platform.release()}",
            "python": sys.version.split()[0],
        },
        "cpu": cpu,
        "ram_gb": ram,
        "gpus": {"nvidia": nvidia, "all_adapters": adapters},
        "runtimes": rt,
        "recommended_profile": asdict(prof),
    }

    if "--json" in sys.argv:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    line = "=" * 64
    print(line)
    print("硬件探测报告")
    print(line)
    print(f"操作系统    : {report['system']['os']}   Python {report['system']['python']}")
    print(f"CPU         : {cpu['name']}")
    print(f"              物理核 {cpu['physical_cores']} / 逻辑核 {cpu['logical_cores']}")
    if cpu["has_igpu"] is False:
        print(f"              [!] {cpu['note']}")
    print(f"内存        : {ram} GB")

    print("\n显示适配器")
    if adapters:
        for a in adapters:
            print(f"  - [{a['vendor']:>7}] {a['name']}  (驱动 {a['driver']})")
    else:
        print("  (未能枚举)")

    print("\nNVIDIA 显卡详情")
    if nvidia:
        for g in nvidia:
            print(
                f"  - {g['name']} | 显存 {g['vram_mb']/1024:.1f} GB | "
                f"驱动 {g['driver']} | 算力 sm_{g['compute_cap'].replace('.','')}"
            )
    else:
        print("  (无)")

    print("\n图形 / 推理运行库")
    print(f"  Vulkan 运行库     : {'可用' if rt['vulkan'] else '不可用'}")
    print(f"  DirectML 运行库   : {'可用' if rt['directml'] else '不可用'}")
    print(f"  CUDA Toolkit      : {'已安装' if rt['cuda_toolkit'] else '未安装（通常无需安装）'}")
    print(f"  TensorRT          : {'已安装' if rt['tensorrt'] else '未安装'}")
    ov = rt.get("openvino") or {}
    if ov.get("installed"):
        print(f"  OpenVINO          : {ov.get('version')} | 可用设备: {', '.join(ov.get('devices') or []) or '无'}")
    else:
        print("  OpenVINO          : 未安装（Intel CPU/集显平台强烈建议安装）")
    if rt["onnxruntime_version"]:
        print(f"  onnxruntime       : {rt['onnxruntime_version']}")
        print(f"    可用 EP         : {', '.join(rt['onnxruntime_providers']) or '无'}")
    else:
        print("  onnxruntime       : 未安装")
    if rt["torch"]:
        print(
            f"  PyTorch           : {rt['torch']['version']} "
            f"(CUDA 可用: {rt['torch']['cuda_available']})"
        )
    else:
        print("  PyTorch           : 未安装")

    print("\n" + line)
    print(f"推荐档位    : {prof.tier} —— {prof.label}")
    print(line)
    print(f"推理后端    : {prof.backend}")
    print(f"精度        : {prof.precision}")
    print(f"tile        : {prof.tile}   tile_pad: {prof.tile_pad}")
    print(f"输入尺寸上限: {prof.max_input_px or '无硬性限制'} px")
    print(f"推荐模型    : {', '.join(prof.models)}")
    print(f"预期表现    : {prof.expected}")
    print(f"自动降级链  : {' → '.join(prof.degradations)}")
    if prof.warnings:
        print("\n注意事项")
        for w in prof.warnings:
            print(f"  [!] {w}")
    print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
