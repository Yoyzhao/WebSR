"""模型目录下载服务（T-713）。

流程：目录条目 → 子进程 curl 流式下载（支持 socks5 代理）→ 复用 F-05 导入链登记
（`.pth` 登记 `needs_convert` 态）→ **自动触发应用内转换**（`conversion_service`，
ADR-003 子进程）→ 产物登记为新 `.onnx` 模型。

三条边界（与 `conversion_service` 同构）：

1. **同一时刻只跑一个下载**：下载占带宽与磁盘，重复触发直接返回当前状态。
2. **代理是配置不是硬编码**：依次取 `ALL_PROXY` / `HTTPS_PROXY` / `HTTP_PROXY`
   环境变量（本机实测 GitHub 直连会长时间停滞，socks5 代理必需）；
   未配置且直连失败时给出**可照做**的指引（结构化错误，不是 500）。
3. **目录外来源一律拒绝**：下载 URL 只来自 `download_catalog.py` 白名单，
   请求方只能传条目 id，不能传 URL——防止应用变成任意文件下载器。
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from ..db import get_session
from ..models.download_catalog import DOWNLOAD_CATALOG, DownloadableModelSpec, get_entry
from ..models.entities import Model
from . import conversion_service
from . import model_registry as reg

logger = logging.getLogger("websr.services.download")

_LOCK = threading.Lock()
_STATE: dict = {
    "status": "idle",       # idle | running | completed | failed
    "entry_id": None,
    "received_bytes": 0,
    "total_bytes": None,
    "model_id": None,       # 登记后的源模型 id（mdl_N）
    "conversion_triggered": False,
    "error": None,          # {code, message, reason}
}

_POLL_INTERVAL_S = 0.4


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _proxy_arg() -> str | None:
    """下载代理取值（T-714）：系统配置 `download_proxy` 优先，留空则跟随环境变量。

    局部 import settings_store：该模块 import 链较重（db/engine），且下载服务在
    API 路由模块加载链上，避免拉长启动 import；同时规避潜在循环 import。
    """
    from . import settings_store

    try:
        configured = settings_store.effective_str("download_proxy").strip()
    except Exception:  # 库不可用时退环境变量，不阻断下载
        configured = ""
    if configured:
        return configured
    for k in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        v = os.environ.get(k)
        if v:
            return v
    return None


def serialize_state() -> dict:
    with _LOCK:
        return dict(_STATE)


def _state_locked() -> dict:
    return dict(_STATE)


def _existing_model(session, entry: DownloadableModelSpec) -> Model | None:
    """该条目是否已下载登记过（幂等：同一权重只登记一次）。"""
    stem = Path(entry.filename).stem
    rows = session.scalars(
        select(Model).where(Model.format == "pth", Model.source == "imported")
    ).all()
    for r in rows:
        if Path(r.path).stem == stem:
            if reg._abs(r.path).is_file():
                return r
    return None


def catalog() -> list[dict]:
    """目录清单（效果优先序）+ 每条的已下载状态 + 转换环境可用性。"""
    s = get_session()
    try:
        conv = conversion_service.describe_availability()
        items = []
        for e in DOWNLOAD_CATALOG:
            have = _existing_model(s, e)
            items.append({
                "id": e.id,
                "name": e.name,
                "scale": e.scale,
                "architecture": e.architecture,
                "style": e.style,
                "license": e.license,
                "size_bytes": e.size_bytes,
                "effect_rank": e.effect_rank,
                "min_vram_mb": e.min_vram_mb,
                "description": e.description,
                "quality_note": e.quality_note,
                "downloaded": have is not None,
                "downloaded_model_id": f"mdl_{have.id}" if have else None,
            })
        return {"items": items, "conversion": conv}
    finally:
        s.close()


def trigger(entry_id: str) -> dict:
    """投递一次下载作业（**异步**，立即返回）。已有作业在跑时不重复触发。"""
    entry = get_entry(entry_id)
    if entry is None:
        from ..core.errors import AppError
        raise AppError(
            "VALIDATION_ERROR", f"下载目录中不存在条目 {entry_id!r}",
            "请刷新目录后重试；只允许下载目录内的模型", 400,
        )
    if shutil.which("curl") is None:
        return {"started": False, "code": "curl_missing",
                "reason": "系统缺少 curl.exe（Windows 10 1803+ 自带），无法执行下载"}

    s = get_session()
    try:
        have = _existing_model(s, entry)
    finally:
        s.close()
    if have is not None:
        return {"started": False, "code": "already_downloaded",
                "reason": f"该模型已下载登记（mdl_{have.id}）", "model_id": f"mdl_{have.id}"}

    with _LOCK:
        if _STATE["status"] == "running":
            return {"started": False, "reason": "已有下载在进行中", **_state_locked()}
        _STATE.update(status="running", entry_id=entry.id, received_bytes=0,
                      total_bytes=entry.size_bytes, model_id=None,
                      conversion_triggered=False, error=None)

    threading.Thread(target=_run, args=(entry,), name="model-download", daemon=True).start()
    return {"started": True, "status": "running", "entry_id": entry.id}


def _run(entry: DownloadableModelSpec) -> None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="websr_download_"))
    try:
        tmp_file = tmp_dir / entry.filename
        proxy = _proxy_arg()
        cmd = ["curl", "-sSL", "--connect-timeout", "30", "--retry", "2",
               "-o", str(tmp_file), entry.url]
        if proxy:
            cmd += ["--proxy", proxy]
        # ⚠️ 剥离继承的代理环境变量（实测踩坑）：宿主 shell 可能带 http_proxy/https_proxy
        # （如沙箱代理，对 GitHub 返回 502 CONNECT），与显式 --proxy 叠加时行为不确定。
        # 代理只认 --proxy 一个来源，确定性优先。
        child_env = {k: v for k, v in os.environ.items()
                     if k.lower() not in ("http_proxy", "https_proxy", "all_proxy", "ftp_proxy")}
        logger.info("开始下载 %s（proxy=%s）", entry.name, bool(proxy))
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=child_env)

        while proc.poll() is None:
            with _LOCK:
                if _STATE["status"] != "running":
                    proc.kill()
                    return
                if tmp_file.is_file():
                    _STATE["received_bytes"] = tmp_file.stat().st_size
            time.sleep(_POLL_INTERVAL_S)

        if proc.returncode != 0:
            return _fail("download_failed",
                         f"下载失败（curl 退出码 {proc.returncode}）",
                         (proc.stderr.read() or b"").decode("utf-8", "replace")[-800:]
                         + ("" if proxy else "\n提示：本机网络访问 GitHub 可能需要代理，"
                            "请在「系统配置 → 网络」填写下载代理（如 socks5://127.0.0.1:7890）后重试，"
                            "或设置环境变量 ALL_PROXY 后重启应用。"))
        if not tmp_file.is_file() or tmp_file.stat().st_size == 0:
            return _fail("download_failed", "下载未产生有效文件", str(tmp_file))
        with _LOCK:
            _STATE["received_bytes"] = tmp_file.stat().st_size

        # 登记：复用 F-05 导入链（校验 / 定名 / 落库全走同一条路）。
        session = get_session()
        try:
            row = reg.save_import(
                session,
                name=entry.name,
                fmt="pth",
                scale=entry.scale,
                min_vram_mb=entry.min_vram_mb,
                backends=["cuda", "cpu", "openvino"],
                primary_tmp=tmp_file,
                primary_filename=entry.filename,
                companion_tmp=None,
                companion_filename=None,
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        # 自动进入应用内转换（环境缺失时保持 needs_convert 态，UI 有既有提示）
        conv = conversion_service.trigger(row.id)
        with _LOCK:
            _STATE.update(model_id=f"mdl_{row.id}",
                          conversion_triggered=bool(conv.get("started")),
                          status="completed")
        logger.info("下载完成并登记：model_id=%s conversion=%s", row.id, conv.get("started"))
    except Exception as exc:
        logger.exception("下载作业异常")
        _fail("download_failed", "下载过程出现未预期错误", f"{type(exc).__name__}: {exc}")
    finally:
        for p in tmp_dir.glob("*"):
            p.unlink(missing_ok=True)
        if tmp_dir.exists() and not any(tmp_dir.iterdir()):
            tmp_dir.rmdir()


def _fail(code: str, message: str, reason: str = "") -> None:
    with _LOCK:
        _STATE.update(status="failed", error={"code": code, "message": message, "reason": reason})
    logger.warning("下载失败 [%s] %s：%s", code, message, reason)
