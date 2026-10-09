"""M5 媒体管理（tech-arch §3）：上传二次校验真实格式 / 落盘 / 缩略图。

设计决策（STEP-5D §T-605）：
- 上传文件**无状态存储**：`data/uploads/<file_id>.<real_ext>`，不落库。
  原图只有被任务引用后才进入 ARTIFACT（task_id 外键非空，上传时点尚无任务），
  为上传单建 FILE 表属过度设计；file_id → 路径靠目录扫描解析，O(少量文件)。
- `real_format` 由 Pillow 实际解码得出（**不信任扩展名**，PRD §3.2）；
  归一化：JPEG→jpg / TIFF→tif，其余小写。
- 缩略图统一转 JPEG（`data/thumbs/<file_id>.jpg`，最长边 512）——预览场景
  不需要透明通道与原格式保真，统一格式让 `/content?variant=thumb` 的
  Content-Type 恒定。
- **EXIF 方向转正**（T-802 增补）：上传即按 `Orientation` 转正，否则缩略图与
  超分结果（M4 预处理会转正）方向不一致，且旁车元信息里的宽高是转正前的值。
"""
from __future__ import annotations

import io
import json
import uuid
from pathlib import Path

from PIL import Image, ImageOps

from ..core.errors import AppError
from . import settings_store

# PRD §3.2 扩展名白名单 ↔ 归一化真实格式
_EXT_TO_FORMAT = {
    ".jpg": "jpg", ".jpeg": "jpg", ".png": "png",
    ".webp": "webp", ".bmp": "bmp", ".tif": "tif", ".tiff": "tif",
}
_PIL_FORMAT_NORMALIZE = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "BMP": "bmp", "TIFF": "tif"}

THUMB_MAX_SIDE = 512
ID_PREFIX = "file_"
#: 任务 id 前缀（与 `tasks/manager.py` 的对外 id 一致；此处只为拼产物目录名）
TASK_ID_PREFIX = "tsk_"


def uploads_dir() -> Path:
    # 走 settings_store 的生效值：F-07 改数据根后，后续上传立即写入新根（不迁移既有文件）
    d = settings_store.effective_data_root() / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def thumbs_dir() -> Path:
    d = settings_store.effective_data_root() / "thumbs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def outputs_dir() -> Path:
    """任务产物根（`data/outputs/`）。T-806 起真实推理结果落在这里。"""
    d = settings_store.effective_data_root() / "outputs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def task_outputs_dir(task_id: int) -> Path:
    """单个任务的产物目录（`data/outputs/tsk_<id>/`），避免同名结果互相覆盖。

    目录名沿用任务的**对外 id 前缀**（`tsk_`，见 `tasks/manager.py`），
    这样磁盘目录与 API 里看到的 `tsk_12` 是同一个标识，排查时不用来回换算。
    """
    d = outputs_dir() / f"{TASK_ID_PREFIX}{task_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _new_file_id() -> str:
    return f"{ID_PREFIX}{uuid.uuid4().hex[:12]}"


def find_upload(file_id: str) -> Path | None:
    """file_id → 原图路径（目录扫描；id 形态非法直接 None，天然防目录逃逸）。
    只匹配图片扩展名，排除同名 `.json` 旁车元信息文件。"""
    if not file_id.startswith(ID_PREFIX) or not file_id[len(ID_PREFIX):].isalnum():
        return None
    for ext in _EXT_TO_FORMAT:
        p = uploads_dir() / f"{file_id}{ext}"
        if p.is_file():
            return p
    return None


def thumb_path(file_id: str) -> Path:
    return thumbs_dir() / f"{file_id}.jpg"


def verify_and_open(data: bytes, filename: str) -> tuple[Image.Image, str]:
    """扩展名白名单 + 真实格式二次校验。返回 (图片, 归一化真实格式)。"""
    ext = Path(filename).suffix.lower()
    if ext not in _EXT_TO_FORMAT:
        raise AppError(
            "UNSUPPORTED_FORMAT",
            "不支持的图片格式",
            "请上传 jpg / png / webp / bmp / tif 格式的图片",
            400,
        )
    try:
        img = Image.open(io.BytesIO(data))
        img.load()  # 强制完整解码，截断文件在此处暴露
    except Exception:
        raise AppError(
            "FORMAT_MISMATCH",
            "文件内容不是有效的图片，或文件已损坏",
            "请确认文件可正常打开后重新上传",
            400,
        )
    real = _PIL_FORMAT_NORMALIZE.get(img.format or "")
    if real is None:
        raise AppError(
            "FORMAT_MISMATCH",
            f"文件实际格式（{img.format}）不在支持范围内",
            "请转换为 jpg / png / webp / bmp / tif 后重新上传",
            400,
        )
    if real != _EXT_TO_FORMAT[ext]:
        raise AppError(
            "FORMAT_MISMATCH",
            f"扩展名（{ext}）与实际格式（{real}）不符",
            "请使用正确的扩展名重新上传（服务端不信任扩展名，已按内容校验）",
            400,
            detail={"ext": ext, "real_format": real},
        )
    # EXIF 方向转正（T-802 增补）：手机竖拍的 Orientation 若不落实，缩略图会是横的、
    # 而超分结果（M4 预处理会转正）是竖的——同一张图两个方向。这里一并转正后，
    # 返回的 width/height 才是真实显示尺寸（会被写进上传元信息，前端按它排版）。
    return ImageOps.exif_transpose(img), real


def make_thumbnail(img: Image.Image, dest: Path) -> None:
    thumb = img.copy()
    thumb.thumbnail((THUMB_MAX_SIDE, THUMB_MAX_SIDE))
    if thumb.mode not in ("RGB", "L"):
        thumb = thumb.convert("RGB")
    thumb.save(dest, "JPEG", quality=85)


def save_upload(data: bytes, filename: str) -> dict:
    """校验 + 落盘 + 缩略图，返回 UploadResult 所需字段。"""
    limit_mb = settings_store.effective_int("max_upload_mb")
    max_bytes = limit_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise AppError(
            "FILE_TOO_LARGE",
            f"文件超过大小上限（{limit_mb} MB）",
            "请压缩图片或降低分辨率后重试",
            413,
        )
    img, real = verify_and_open(data, filename)
    file_id = _new_file_id()
    dest = uploads_dir() / f"{file_id}.{real}"
    dest.write_bytes(data)
    make_thumbnail(img, thumb_path(file_id))
    meta = {
        "file_id": file_id,
        "filename": Path(filename).name,
        "size": len(data),
        "width": img.width,
        "height": img.height,
        "real_format": real,
    }
    # 旁车元信息：上传不落库（T-605 决策），任务中心凭 file_id 反查文件名/尺寸
    (uploads_dir() / f"{file_id}.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )
    return meta


def read_meta(file_id: str) -> dict | None:
    """上传旁车元信息（文件名 / 尺寸 / 真实格式）；不存在或损坏返回 None。"""
    if not file_id.startswith(ID_PREFIX) or not file_id[len(ID_PREFIX):].isalnum():
        return None
    p = uploads_dir() / f"{file_id}.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def get_content(file_id: str, variant: str) -> tuple[Path, str]:
    """返回 (路径, media_type)。variant=result 属任务产物，随 T-606/T-808 接入。"""
    if variant == "original":
        p = find_upload(file_id)
        if p is None:
            raise AppError("NOT_FOUND", "文件不存在或已被清理", "请重新上传图片", 404)
        media = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp",
                 "bmp": "image/bmp", "tif": "image/tiff"}[p.suffix.lower().lstrip(".").replace("jpeg", "jpg")]
        return p, media
    if variant == "thumb":
        p = thumb_path(file_id)
        if not p.is_file():
            # 兼容缩略图缺失（如历史数据）：有原图则即时补生成
            src = find_upload(file_id)
            if src is None:
                raise AppError("NOT_FOUND", "文件不存在或已被清理", "请重新上传图片", 404)
            with Image.open(src) as img:
                img.load()
                make_thumbnail(ImageOps.exif_transpose(img), p)
        return p, "image/jpeg"
    raise AppError(
        "VALIDATION_ERROR",
        f"不支持的 variant: {variant}",
        "当前支持 original / thumb；result（任务产物）随任务中心接入",
        400,
    )
