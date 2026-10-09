"""可选推理运行时的可用性探测（模型格式路由的前置；ADR-003 / PRD §7.5 / T-806）。

四种格式需要的运行时不同，而**应用环境只保证 ORT**：

| 格式 | 运行时 | 应用环境（`.venvs/sr-app`）中的定位 |
|---|---|---|
| `onnx` | `onnxruntime` | **主路径**，必备 |
| `openvino_ir` | `openvino` | 可选（Intel 原生路径；ORT+OV EP 实测为负收益，不走） |
| `ncnn` | `ncnn` | 可选（ADR-003：条件支持，加载失败即剔除，不影响其它格式） |
| `pth` / `safetensors` | ——（需离线转换） | **本应用不加载**，由 T-807 转换工具转成 `.onnx` |

## 为什么"未安装"必须是一种状态，而不是一个异常

ADR-003 把 ncnn 定位为可选后端，PRD §2.7 把 `.bin`/`.pth`/`.safetensors` 归入 **S2**
并明确约束"任何 S2 项的失败都不得影响 S1 主链路"。因此缺运行时必须是**可查询的状态**
（`available=False` + 一句能照做的 `reason`），而不是让 import 崩掉整条链路。
这也与阶段 A "`probes_skipped` 与 `probes_failed` 分开记"的纪律同源：**没装组件不是故障**。

## 纪律

- 用 `importlib.util.find_spec` / `importlib.metadata.version` 探测，**绝不 import 运行时本尊**——
  一次 `import openvino` 是秒级、数百 MB 内存，探测不能成为启动或列表请求的瓶颈；
- 探测**永不抛异常**（同阶段 A），取不到就如实记 `None` 并说明。
"""
from __future__ import annotations

import importlib.metadata as importlib_metadata
import importlib.util
import logging
from dataclasses import asdict, dataclass

logger = logging.getLogger("websr.engine.runtimes")

__all__ = [
    "RUNTIME_BY_FORMAT",
    "OPTIONAL_RUNTIMES",
    "RuntimeStatus",
    "module_available",
    "module_version",
    "status_for_module",
    "status_for_format",
    "is_format_loadable",
    "describe_runtimes",
]

#: 格式 → 运行时包名（`None` = 本应用不加载该格式，需离线转换）
RUNTIME_BY_FORMAT: dict[str, str | None] = {
    "onnx": "onnxruntime",
    "openvino_ir": "openvino",
    "ncnn": "ncnn",
    "pth": None,
    "safetensors": None,
}

#: 可选运行时（缺失属正常部署形态，不算故障；用于诊断与 UI 说明）
OPTIONAL_RUNTIMES: tuple[str, ...] = ("openvino", "ncnn")

_INSTALL_HINT: dict[str, str] = {
    "openvino": "在当前环境执行 `uv pip install openvino` 后重启后端，即可加载 OpenVINO IR 模型",
    "ncnn": "在当前环境执行 `uv pip install ncnn --no-deps` 后重启后端，即可加载 ncnn 模型",
}

_NOT_LOADABLE_REASON = {
    "pth": "PyTorch 权重（.pth）需先离线转换为 .onnx 才能加载（应用内不捆绑 PyTorch，见 ADR-003）",
    "safetensors": "safetensors 权重需先离线转换为 .onnx 才能加载（应用内不捆绑 PyTorch，见 ADR-003）",
}


@dataclass(frozen=True)
class RuntimeStatus:
    """一个运行时的可用性事实（`module=None` 表示该格式本应用不加载）。"""

    module: str | None
    available: bool
    version: str | None
    reason: str | None
    optional: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def module_available(module: str) -> bool:
    """模块能否被导入（只查 spec，不真的 import）。**永不抛异常。**"""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError, AttributeError, TypeError) as exc:
        # 父包缺失 / 命名空间包异常等：按不可用处理，但留下痕迹
        logger.debug("探测模块 %s 失败（按不可用处理）: %s", module, exc)
        return False


def module_version(module: str) -> str | None:
    """已安装发行包版本；查不到返回 None。**不 import 模块本体。**"""
    try:
        return importlib_metadata.version(module)
    except Exception:  # PackageNotFoundError 及其它元数据异常
        return None


def status_for_module(module: str | None) -> RuntimeStatus:
    if module is None:
        return RuntimeStatus(module=None, available=False, version=None,
                             reason="该格式不支持直接加载", optional=False)
    available = module_available(module)
    version = module_version(module) if available else None
    optional = module in OPTIONAL_RUNTIMES
    if available:
        reason = None
    else:
        reason = _INSTALL_HINT.get(module, f"当前环境未安装 {module} 运行时")
    return RuntimeStatus(module=module, available=available, version=version,
                         reason=reason, optional=optional)


def status_for_format(fmt: str) -> RuntimeStatus:
    """按模型格式问"现在能不能加载"。未知格式按不可用处理并说明。"""
    if fmt not in RUNTIME_BY_FORMAT:
        return RuntimeStatus(module=None, available=False, version=None,
                             reason=f"未知的模型格式: {fmt}", optional=False)
    module = RUNTIME_BY_FORMAT[fmt]
    if module is None:
        return RuntimeStatus(module=None, available=False, version=None,
                             reason=_NOT_LOADABLE_REASON.get(fmt, "该格式需离线转换"),
                             optional=False)
    return status_for_module(module)


def is_format_loadable(fmt: str) -> bool:
    """该格式此刻是否具备加载所需的运行时。"""
    return status_for_format(fmt).available


def describe_runtimes() -> dict:
    """诊断用汇总：格式 → 运行时状态 + 可选运行时一行摘要。"""
    by_format = {fmt: status_for_format(fmt).to_dict() for fmt in RUNTIME_BY_FORMAT}
    optional = {
        name: {
            "available": module_available(name),
            "version": module_version(name) if module_available(name) else None,
            "reason": None if module_available(name) else _INSTALL_HINT.get(name),
        }
        for name in OPTIONAL_RUNTIMES
    }
    return {
        "by_format": by_format,
        "optional": optional,
        "note": (
            "应用环境只保证 onnxruntime；openvino / ncnn 属可选后端，"
            "缺失时对应格式不可加载但不影响 S1 主链路（ADR-003 / PRD §2.7）"
        ),
    }
