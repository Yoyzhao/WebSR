"""`.pth` / `.safetensors` → `.onnx` 的**离线转换入口**（ADR-003 方案 A / PRD §7.5 / T-807）。

ADR-003 的两条硬约束决定了本模块的形态：

1. **应用内不捆绑 PyTorch** → 本模块**只做三件事**：定位转换环境、**以子进程**调用
   `tools/convert_to_onnx.py`、把产物登记成新的 `.onnx` 模型。产品代码**不 import torch**，
   也不 import 转换工具（`tools/` 是开发期资产，产品不得依赖）。2. **"若无法识别则明确报错而非猜测"** → 架构识别在工具侧由 spandrel 完成；
   识别不出时工具给出 `arch_unrecognized` 与候选列表，本模块**原样透出**，
   不做"换个架构再试试"的兜底。

三条边界（与 `calibration_service` 同构）:

1. **转换作业不进 `TASK` 表**——它是系统级操作，不是用户的图片处理任务。
2. **同一时刻只跑一个转换**：导出吃 CPU 与内存（RRDBNet 导出实测数十秒），
   并发只会互相拖慢；重复触发直接返回当前状态。
3. **环境缺失是状态而非异常**：`.venvs/sr-convert` 不存在时，返回**可照做的安装指引**，
   而不是让请求 500——与 `engine/runtimes.py` 对可选后端的口径一致。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from ..core.config import PROJECT_ROOT
from ..db import get_session
from ..models.entities import Model
from . import model_registry

logger = logging.getLogger("websr.services.conversion")

CONVERT_ENV_DIR = PROJECT_ROOT / ".venvs" / "sr-convert"
CONVERT_TOOL = PROJECT_ROOT / "tools" / "convert_to_onnx.py"

#: 转换耗时的**物理下界参考**：RRDBNet(67 MB) 导出实测数十秒（构建整图 + 常量折叠）。
#: 给足余量，但不无限等——超时按失败处理并保留诊断信息。
DEFAULT_TIMEOUT_S = 900

CONVERTIBLE_FORMATS = ("pth", "safetensors")

_INSTALL_HINT = (
    "转换需要与主应用**分离**的 Python 环境（ADR-003：主应用不含 torch）。"
    "在项目根目录执行：\n"
    "  uv venv --python 3.13 .venvs/sr-convert\n"
    "  uv pip install --python .venvs/sr-convert/Scripts/python.exe torch torchvision "
    "--index-url https://download.pytorch.org/whl/cpu\n"
    "  uv pip install --python .venvs/sr-convert/Scripts/python.exe spandrel safetensors "
    "onnx onnxruntime numpy"
)

_LOCK = threading.Lock()
_STATE: dict = {
    "job_id": None,
    "status": "idle",  # idle | running | completed | failed
    "model_id": None,
    "started_at": None,
    "finished_at": None,
    "result": None,   # 工具 JSON 摘要 + 登记后的模型 id
    "error": None,    # {code, message, reason}
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 环境探测（"未安装"是状态）
# ---------------------------------------------------------------------------

def env_python() -> Path | None:
    """转换环境的解释器路径；不存在返回 None（**不尝试 import torch**）。"""
    for rel in ("Scripts/python.exe", "bin/python"):
        p = CONVERT_ENV_DIR / rel
        if p.is_file():
            return p
    return None


def describe_availability() -> dict:
    """`ModelOut.conversion` 的取值来源。"""
    py = env_python()
    if py is None:
        return {"available": False, "reason": f"转换环境未安装（{CONVERT_ENV_DIR}）。{_INSTALL_HINT}"}
    if not CONVERT_TOOL.is_file():
        return {"available": False, "reason": f"转换工具缺失：{CONVERT_TOOL}"}
    return {"available": True, "reason": None}


def serialize_state() -> dict:
    with _LOCK:
        return {
            "job_id": _STATE["job_id"],
            "status": _STATE["status"],
            "model_id": _STATE["model_id"],
            "started_at": _STATE["started_at"],
            "finished_at": _STATE["finished_at"],
            "result": _STATE["result"],
            "error": _STATE["error"],
        }


def reset_state() -> None:
    """测试用：清空进程内转换状态。"""
    with _LOCK:
        _STATE.update(job_id=None, status="idle", model_id=None, started_at=None,
                      finished_at=None, result=None, error=None)


# ---------------------------------------------------------------------------
# 触发
# ---------------------------------------------------------------------------

def trigger(model_id: int) -> dict:
    """投递一次转换作业（**异步**，立即返回）。已有作业在跑时不重复触发。"""
    avail = describe_availability()
    if not avail["available"]:
        return {"started": False, "reason": avail["reason"], "code": "convert_env_missing"}

    with _LOCK:
        if _STATE["status"] == "running":
            return {"started": False, "reason": "已有转换在进行中", **_state_locked()}

        job_id = f"conv_{int(time.time())}"
        _STATE.update(job_id=job_id, status="running", model_id=model_id,
                      started_at=_now_iso(), finished_at=None, result=None, error=None)
        target = job_id

    threading.Thread(target=_run, args=(target, model_id), name="conversion", daemon=True).start()
    return {"started": True, "task_id": target, "status": "running"}


def _state_locked() -> dict:
    return {
        "job_id": _STATE["job_id"],
        "status": _STATE["status"],
        "model_id": _STATE["model_id"],
        "started_at": _STATE["started_at"],
        "finished_at": _STATE["finished_at"],
        "result": _STATE["result"],
        "error": _STATE["error"],
    }


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------

def _converted_name(src_model: Model) -> str:
    """产物定名含源模型 id → 对同一源模型天然幂等，重复触发不会越堆越多。"""
    return f"{Path(src_model.path).stem}__from{src_model.id}.onnx"


def _run(job_id: str, model_id: int) -> None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="websr_convert_"))
    try:
        session = get_session()
        try:
            src_model = session.get(Model, model_id)
            if src_model is None:
                return _fail("model_not_found", "指定的模型不存在", "请刷新模型列表后重试")
            if src_model.format not in CONVERTIBLE_FORMATS:
                return _fail(
                    "not_convertible",
                    f"{src_model.format} 格式无需转换",
                    "转换入口只对 .pth / .safetensors 开放；其它格式请直接导入或加载",
                )
            src_path = model_registry._abs(src_model.path)
            if not src_path.is_file():
                return _fail("file_missing", "源权重文件不存在或已被移动", str(src_path))

            out_name = _converted_name(src_model)
            scale = int(src_model.scale or 0)
            if scale not in (2, 3, 4):
                # 应用侧只支持 2/3/4 倍；不在这里"改成 4"——那是替用户做决定
                return _fail(
                    "unsupported_scale",
                    f"模型声明的超分倍数为 {scale}，应用侧仅支持 2 / 3 / 4",
                    "请修正模型元信息中的倍数后重新导入，或换用受支持的权重",
                )

            # 已转换过：直接复用（导出一次实测数十秒，重复点击不该重跑）
            reused = _find_existing(session, out_name)
            if reused is not None:
                logger.info("转换复用已有产物：%s", out_name)
                return _done({"reused": True, "model_id": f"mdl_{reused.id}",
                              "model_name": reused.name, "path": f"data/models/{reused.path}"})

            py = env_python()
            if py is None:
                return _fail("convert_env_missing", "转换环境未安装", _INSTALL_HINT)

            logger.info("开始转换：%s → %s（interpreters=%s）", src_path.name, out_name, py)
            tmp_out = tmp_dir / out_name
            proc, payload = _invoke(py, src_path, tmp_out)

            if not payload.get("ok"):
                code = payload.get("code") or "convert_failed"
                return _fail(
                    code,
                    payload.get("message") or f"转换失败（退出码 {proc.returncode}）",
                    payload.get("reason") or (proc.stderr or "")[-1500:],
                    **{k: v for k, v in payload.items() if k not in ("ok", "code", "message", "reason")},
                )

            produced = Path(payload["output"]["path"])
            if not produced.is_file():
                return _fail("convert_failed", "转换报告成功但产物不存在", str(produced))

            # 登记：复用 F-05 导入链（校验 / 定名 / 去重 / 落库全走同一条路）。
            # ⚠️ **不要把产物预放到 imported/<name>**：`save_import` 见到同名文件会改名成
            # `…_1.onnx`，`_find_existing` 的定名约定随即失配 → 幂等复用失效（实测踩过）。
            # 正确做法是把临时文件交给它，由它负责移入并按需定名。
            try:
                row = model_registry.save_import(
                    session,
                    name=f"{src_model.name}（转换）",
                    fmt="onnx",
                    scale=scale,
                    min_vram_mb=int(src_model.min_vram_mb or 0),
                    backends=list(src_model.supported_backends or ["cpu"]),
                    primary_tmp=produced,
                    primary_filename=out_name,
                    companion_tmp=None,
                    companion_filename=None,
                )
                session.commit()
            except Exception:
                session.rollback()
                raise

            logger.info("转换完成并登记：model_id=%s name=%s", row.id, row.name)
            return _done({
                "reused": False,
                "model_id": f"mdl_{row.id}",
                "model_name": row.name,
                "path": f"data/models/{row.path}",
                "tool": {
                    "architecture": payload.get("model", {}).get("architecture"),
                    "scale": payload.get("model", {}).get("scale"),
                    "param_count": payload.get("model", {}).get("param_count"),
                    "size_requirements": payload.get("model", {}).get("size_requirements"),
                    "check": payload.get("check"),
                    "output": payload.get("output"),
                },
            })
        finally:
            session.close()
    except Exception as exc:  # 兜底：作业线程绝不能把异常抛到解释器外
        logger.exception("转换作业异常")
        _fail("convert_failed", "转换过程出现未预期错误", f"{type(exc).__name__}: {exc}")
    finally:
        for p in tmp_dir.glob("*"):
            p.unlink(missing_ok=True)
        tmp_dir.rmdir() if tmp_dir.exists() and not any(tmp_dir.iterdir()) else None


def _find_existing(session, out_name: str) -> Model | None:
    """产物已登记过就复用——不是"猜测"，而是同一输入只该有一次产物。"""
    stem = Path(out_name).stem
    rows = session.scalars(
        select(Model).where(Model.format == "onnx", Model.source == "imported")
    ).all()
    for r in rows:
        if Path(r.path).stem == stem:
            p = model_registry._abs(r.path)
            if p.is_file() and p.stat().st_size > 0:
                return r
    return None


def _invoke(py: Path, src: Path, out: Path) -> tuple[subprocess.CompletedProcess, dict]:
    """以**子进程**调用转换工具。工具与产品代码依赖完全分离（ADR-003）。"""
    cmd = [str(py), str(CONVERT_TOOL), str(src), "-o", str(out), "--json"]
    env = dict(os.environ)
    # 让工具的 stdout 只有 JSON；torch 的进度/警告走 stderr
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.run(
        cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True,
        timeout=DEFAULT_TIMEOUT_S, env=env, encoding="utf-8", errors="replace",
    )
    payload: dict = {}
    try:
        payload = json.loads(proc.stdout)
    except Exception:
        payload = {
            "ok": False,
            "code": "convert_failed",
            "message": f"转换工具未返回可解析的结果（退出码 {proc.returncode}）",
            "reason": (proc.stdout or "")[-800:] + (proc.stderr or "")[-800:],
        }
    return proc, payload


# ---------------------------------------------------------------------------
# 状态收尾
# ---------------------------------------------------------------------------

def _done(result: dict) -> None:
    with _LOCK:
        _STATE.update(status="completed", finished_at=_now_iso(), result=result, error=None)


def _fail(code: str, message: str, reason: str = "", **extra) -> None:
    err = {"code": code, "message": message, "reason": reason}
    err.update(extra)
    with _LOCK:
        _STATE.update(status="failed", finished_at=_now_iso(), error=err, result=None)
    logger.warning("转换失败 [%s] %s：%s", code, message, reason)
