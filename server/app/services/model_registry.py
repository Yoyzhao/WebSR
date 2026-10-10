"""M3 模型库服务（tech-arch §3）：登记 / 格式路由 / 哈希 / 导入导出 / 删除。

职责边界：
- 只做**登记与元信息管理**；模型的真实加载与元信息提取（参数量 / 动态 shape）
  属 T-806 加载器，当前 `capabilities` 对导入模型给保守默认值（已挂账）；
- 模型文件落盘到 `data/models/imported/`（内置模型直接引用 `data/models/` 既有
  文件，不复制）。"模型目录只读"约束指**推理运行期**不得改写模型文件，导入是
  用户显式动作，落在独立子目录；
- sha256 对 IR/ncnn 取「主文件 + 配套文件」的**组合哈希**，保证权重体变化
  也能触发去重与（未来的）标定失效（tech-arch §4.2 失效判据含模型哈希）。
"""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.errors import AppError
from . import settings_store
from ..db import get_session
from ..engine.availability import HardwareSnapshot, gate_availability
from ..models.builtin_catalog import BUILTIN_MODELS, BuiltinModelSpec
from ..models.entities import MODEL_FORMATS, Model
from ..schemas.model import ModelCapabilities, ModelConversion, ModelOut

logger = logging.getLogger("websr.services.model_registry")

ID_PREFIX = "mdl_"
CHUNK = 1024 * 1024

# 各格式的主文件扩展名与配套要求（ADR-003 格式路由的登记表达）
_FORMAT_RULES: dict[str, dict] = {
    "onnx": {"primary_ext": ".onnx", "companion_ext": None},
    "openvino_ir": {"primary_ext": ".xml", "companion_ext": ".bin"},
    "ncnn": {"primary_ext": ".param", "companion_ext": ".bin"},
    "pth": {"primary_ext": ".pth", "companion_ext": None},
    "safetensors": {"primary_ext": ".safetensors", "companion_ext": None},
}


# ---------------------------------------------------------------------------
# 路径与哈希工具
# ---------------------------------------------------------------------------

def models_dir() -> Path:
    # 由数据根派生（PRD §4.2）；F-07 改数据根后后续读取立即指向新根
    return settings_store.effective_model_dir()


def imported_dir() -> Path:
    d = models_dir() / "imported"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _abs(rel_path: str) -> Path:
    """DB 存相对 data/models/ 的路径；解析为绝对路径并防目录逃逸。"""
    root = models_dir().resolve()
    p = (root / rel_path).resolve()
    if root not in p.parents and p != root:
        raise AppError("INTERNAL_ERROR", "模型路径异常", "请导出诊断 JSON 并查看日志定位原因", 500)
    return p


def _hash_file(hasher: "hashlib._Hash", path: Path) -> None:
    with path.open("rb") as f:
        while True:
            buf = f.read(CHUNK)
            if not buf:
                break
            hasher.update(buf)


def combined_sha256(primary: Path, companion: Path | None = None) -> str:
    """主文件（+配套文件）组合哈希。"""
    h = hashlib.sha256()
    _hash_file(h, primary)
    if companion is not None:
        h.update(b"\x00companion\x00")
        _hash_file(h, companion)
    return h.hexdigest()


def _safe_filename(name: str) -> str:
    """去掉路径成分与危险字符，只保留文件名本身。"""
    base = Path(name).name
    return re.sub(r"[^\w.\-一-鿿]", "_", base)


# ---------------------------------------------------------------------------
# 序列化（DB → 契约 Model）
# ---------------------------------------------------------------------------

_CATALOG_BY_PATH: dict[str, BuiltinModelSpec] = {spec.path: spec for spec in BUILTIN_MODELS}

# 导入模型的能力默认值（T-806 加载器到位后由真实元信息替换；已挂账）
def default_capabilities(fmt: str) -> dict:
    return {
        "supports_fp16": fmt == "onnx",
        "supports_batch": False,
        "supports_tile0": fmt in ("onnx", "openvino_ir"),
        "has_tensorrt": False,
        "is_generative": False,
        "num_inference_steps": None,
        "requires_prompt": False,
    }


def serialize_model(m: Model, snapshot: HardwareSnapshot) -> ModelOut:
    spec = _CATALOG_BY_PATH.get(m.path)
    primary = _abs(m.path)
    companion_path = _abs(m.companion_path) if m.companion_path else None

    size = 0
    for p in (primary, companion_path):
        if p is not None and p.is_file():
            size += p.stat().st_size

    companion = None
    if m.companion_path:
        companion = [Path(m.path).suffix.lower(), Path(m.companion_path).suffix.lower()]

    available, reason = gate_availability(
        status=m.status, min_vram_mb=m.min_vram_mb, snapshot=snapshot, fmt=m.format
    )

    # 局部 import：conversion_service 反向依赖本模块，模块级 import 会成环
    from . import conversion_service  # noqa: PLC0415

    conversion = None
    if m.format in conversion_service.CONVERTIBLE_FORMATS:
        conversion = ModelConversion(**conversion_service.describe_availability())

    return ModelOut(
        id=f"{ID_PREFIX}{m.id}",
        name=m.name,
        architecture=spec.architecture if spec else m.format,
        description=spec.description if spec else "用户导入模型",
        format=m.format,
        path=f"data/models/{m.path}",
        sha256=m.sha256,
        size_bytes=size,
        params_count=m.param_count,
        scale=m.scale,
        license=m.license,
        source=m.source,
        min_vram_mb=m.min_vram_mb,
        supported_backends=list(m.supported_backends or []),
        capabilities=ModelCapabilities(
            **(spec.capabilities if spec else default_capabilities(m.format))
        ),
        companion=companion,
        available=available,
        unavailable_reason=reason,
        status=m.status,
        conversion=conversion,
    )


def parse_model_id(raw: str) -> int:
    if not raw.startswith(ID_PREFIX) or not raw[len(ID_PREFIX):].isdigit():
        raise AppError("MODEL_NOT_FOUND", "指定的模型不存在", "请刷新模型列表后重试", 404)
    return int(raw[len(ID_PREFIX):])


def get_model_or_404(s: Session, raw_id: str) -> Model:
    m = s.get(Model, parse_model_id(raw_id))
    if m is None:
        raise AppError("MODEL_NOT_FOUND", "指定的模型不存在", "请刷新模型列表后重试", 404)
    return m


# ---------------------------------------------------------------------------
# 内置模型同步（启动时调用；幂等）
# ---------------------------------------------------------------------------

def sync_builtin_models() -> None:
    """把 catalog 中存在的文件登记/刷新进库。文件缺失跳过并告警，不阻断启动。"""
    root = models_dir()
    session = get_session()
    try:
        for spec in BUILTIN_MODELS:
            primary = root / spec.path
            companion = (root / spec.path).parent / spec.companion_name if spec.companion_name else None
            if not primary.is_file():
                logger.warning("内置模型文件缺失，跳过登记: %s", primary)
                continue
            if spec.companion_name and not (companion and companion.is_file()):
                logger.warning("内置模型配套文件缺失，跳过登记: %s", companion)
                continue

            companion_rel = (
                str(Path(spec.path).parent / spec.companion_name) if spec.companion_name else None
            )
            row = session.scalar(select(Model).where(Model.path == spec.path))
            # 快速短路：尺寸未变则跳过重算哈希（67MB×3 全量哈希约 1s，可接受但没必要）
            need_hash = row is None or (row.sha256 == "")
            if row is not None and primary.stat().st_size != 0:
                need_hash = False  # 内容级变化（同尺寸改写）属极端场景，以重装/手动刷新覆盖
            sha = combined_sha256(primary, companion) if need_hash else row.sha256

            if row is None:
                session.add(Model(
                    name=spec.name, format=spec.format, companion_path=companion_rel,
                    supported_backends=spec.supported_backends, path=spec.path, sha256=sha,
                    param_count=spec.params_count, scale=spec.scale,
                    min_vram_mb=spec.min_vram_mb, license=spec.license,
                    source="builtin", status="ready",
                ))
            else:
                row.name = spec.name
                row.format = spec.format
                row.companion_path = companion_rel
                row.supported_backends = spec.supported_backends
                row.sha256 = sha
                row.param_count = spec.params_count
                row.scale = spec.scale
                row.min_vram_mb = spec.min_vram_mb
                row.license = spec.license
                row.source = "builtin"
                row.status = "ready"
        session.commit()
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 列表 / 导入 / 导出 / 删除
# ---------------------------------------------------------------------------

def list_models(s: Session, snapshot: HardwareSnapshot, fmt: str | None = None) -> list[ModelOut]:
    # builtin 在前（'builtin' < 'imported' 字典序升序），同来源内按登记时间
    stmt = select(Model).order_by(Model.source.asc(), Model.created_at.asc(), Model.id.asc())
    if fmt:
        if fmt not in MODEL_FORMATS:
            raise AppError("VALIDATION_ERROR", "未知的模型格式筛选值", "请检查筛选条件后重试", 400)
        stmt = stmt.where(Model.format == fmt)
    return [serialize_model(m, snapshot) for m in s.scalars(stmt).all()]


def save_import(
    s: Session,
    *,
    name: str,
    fmt: str,
    scale: int,
    min_vram_mb: int,
    backends: list[str],
    primary_tmp: Path,
    primary_filename: str,
    companion_tmp: Path | None,
    companion_filename: str | None,
) -> Model:
    """导入登记。临时文件由路由层落盘（流式）；本函数负责校验、定名、入库。"""
    rule = _FORMAT_RULES.get(fmt)
    if rule is None:
        raise AppError("UNSUPPORTED_FORMAT", "不支持的模型格式", "请导入 .onnx / .xml / .param / .pth / .safetensors 模型文件", 400)

    ext = Path(primary_filename).suffix.lower()
    if ext == ".bin":
        raise AppError(
            "MODEL_MISSING_COMPANION",
            ".bin 是权重体，必须与 .xml（OpenVINO IR）或 .param（ncnn）一同提供",
            "请重新选择 .xml 或 .param 作为主文件，并把 .bin 作为配套文件一起导入",
            400,
        )
    if ext != rule["primary_ext"]:
        raise AppError(
            "UNSUPPORTED_FORMAT",
            f"{fmt} 格式的主文件应为 {rule['primary_ext']}",
            "请确认选择的文件与格式匹配后重试",
            400,
        )
    if rule["companion_ext"] is not None:
        if companion_tmp is None or not companion_filename:
            raise AppError(
                "MODEL_MISSING_COMPANION",
                f"{fmt} 格式必须同时提供 {rule['companion_ext']} 配套文件",
                "请将配套文件与主文件一起导入",
                400,
            )
        if Path(companion_filename).suffix.lower() != rule["companion_ext"]:
            raise AppError(
                "MODEL_MISSING_COMPANION",
                f"配套文件应为 {rule['companion_ext']}",
                "请确认配套文件选择正确后重试",
                400,
            )

    sha = combined_sha256(primary_tmp, companion_tmp)
    existing = s.scalar(select(Model).where(Model.sha256 == sha))
    if existing is not None:
        raise AppError(
            "MODEL_ALREADY_EXISTS",  # 契约 §2.3 无专用码，新增码列入 T-700 冻结核对
            f"相同文件已登记为「{existing.name}」",
            "请直接使用模型列表中的已有模型",
            409,
        )

    dest_dir = imported_dir()
    dest = dest_dir / _safe_filename(primary_filename)
    n = 1
    while dest.exists():
        dest = dest_dir / f"{dest.stem}_{n}{dest.suffix}"
        n += 1
    companion_dest: Path | None = None
    if companion_tmp is not None and companion_filename:
        companion_dest = dest.with_name(_safe_filename(companion_filename))
        n = 1
        while companion_dest.exists():
            companion_dest = dest_dir / f"{Path(companion_dest.stem).stem}_{n}{companion_dest.suffix}"
            n += 1

    primary_tmp.replace(dest)
    rel = dest.relative_to(models_dir()).as_posix()
    companion_rel = None
    if companion_tmp is not None and companion_dest is not None:
        companion_tmp.replace(companion_dest)
        companion_rel = companion_dest.relative_to(models_dir()).as_posix()

    model = Model(
        name=name.strip(),
        format=fmt,
        companion_path=companion_rel,
        supported_backends=backends,
        path=rel,
        sha256=sha,
        param_count=None,  # T-806 加载器到位后回填真实参数量
        scale=scale,
        min_vram_mb=min_vram_mb,
        license="未标注",
        source="imported",
        status="needs_convert" if fmt in ("pth", "safetensors") else "ready",
    )
    s.add(model)
    s.commit()
    s.refresh(model)
    return model


def delete_model(s: Session, raw_id: str) -> None:
    m = get_model_or_404(s, raw_id)
    if m.source == "builtin":
        raise AppError(
            "MODEL_BUILTIN_READONLY",  # 新增码，列入 T-700 冻结核对
            "内置模型随应用提供，不能删除",
            "如不需要可忽略；内置模型不占用额外空间",
            409,
        )
    # 只删登记记录，不删磁盘文件（与前端确认弹窗文案一致）
    s.delete(m)
    s.commit()
