"""模型库路由（api-contract §4.3）。

差异登记（T-700 冻结核对）：
- `GET /api/models` 返回 **`Model[]` 数组**（与前端已定稿类型 `fetchModels(): Model[]`
  一致），契约草案的分页包装 `{items,total,...}` 是否启用待冻结时裁决；
- 模型导入**不套用** `APP_MAX_UPLOAD_MB`（那是图片上传上限，67MB 的内置模型
  已超）；模型大小上限是否需要单列，待 T-700；
- `POST /api/models/{id}/convert`（.pth/.safetensors 离线转换入口）**已由 T-807 实现**：
  应用内**不捆绑 PyTorch**，转换在独立环境 `.venvs/sr-convert` 里**以子进程**完成
  （ADR-003 方案 A）。触发为**异步**（导出实测数十秒，同步等待会顶穿前端超时），
  故配一个 `GET` 取作业状态与结果；
- 导出只给主文件（IR/ncnn 的配套 .bin 打包导出形式待 T-700 裁决）。
"""
import json
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import FileResponse, Response

from ..core.errors import AppError
from ..db import get_session
from ..engine.availability import probe_hardware_snapshot
from ..schemas.model import ModelOut
from ..services import conversion_service
from ..services import model_registry as reg

router = APIRouter(prefix="/api/models", tags=["models"])

_BACKENDS = {"cpu", "cuda", "openvino", "tensorrt", "ncnn"}


@router.get("")
def list_models(format: str | None = Query(default=None)) -> list[ModelOut]:
    s = get_session()
    try:
        return reg.list_models(s, probe_hardware_snapshot(), fmt=format)
    finally:
        s.close()


async def _spool(upload: UploadFile, tmp_dir: Path) -> Path:
    """流式落盘到临时文件（不读入内存），返回临时路径。"""
    fd, tmp = tempfile.mkstemp(dir=tmp_dir, prefix="upload_")
    tmp_path = Path(tmp)
    with open(fd, "wb") as f:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
    return tmp_path


@router.post("/import", status_code=201)
async def import_model(
    name: str = Form(...),
    format: str = Form(...),
    scale: int = Form(...),
    min_vram_mb: int = Form(...),
    backends: str = Form(...),  # JSON 数组字符串，如 '["cuda","cpu"]'
    file: UploadFile = File(...),
    companion_file: UploadFile | None = File(default=None),
) -> ModelOut:
    if not name.strip():
        raise AppError("VALIDATION_ERROR", "模型名称不能为空", "请填写模型名称后重试", 400)
    try:
        backend_list = json.loads(backends)
        assert isinstance(backend_list, list) and all(isinstance(b, str) for b in backend_list)
    except (json.JSONDecodeError, AssertionError):
        raise AppError("VALIDATION_ERROR", "backends 应为 JSON 数组字符串", '形如 ["cuda","cpu"]', 400)
    unknown = set(backend_list) - _BACKENDS
    if unknown:
        raise AppError(
            "VALIDATION_ERROR", f"未知后端标识: {sorted(unknown)}",
            f"可用值为 {sorted(_BACKENDS)}", 400,
        )
    if scale not in (2, 3, 4):
        raise AppError("VALIDATION_ERROR", "超分倍数仅支持 2 / 3 / 4", "请调整倍数后重试", 400)

    tmp_dir = Path(tempfile.mkdtemp(prefix="websr_import_"))
    primary_tmp = await _spool(file, tmp_dir)
    companion_tmp = await _spool(companion_file, tmp_dir) if companion_file else None
    try:
        s = get_session()
        try:
            model = reg.save_import(
                s,
                name=name, fmt=format, scale=scale, min_vram_mb=min_vram_mb,
                backends=backend_list,
                primary_tmp=primary_tmp, primary_filename=file.filename or "model",
                companion_tmp=companion_tmp,
                companion_filename=companion_file.filename if companion_file else None,
            )
            return reg.serialize_model(model, probe_hardware_snapshot())
        finally:
            s.close()
    finally:
        # 校验失败 / 已入库移动后，清理残留临时文件
        for p in (primary_tmp, companion_tmp):
            if p is not None and p.exists():
                p.unlink(missing_ok=True)
        tmp_dir.rmdir() if not any(tmp_dir.iterdir()) else None


@router.get("/{model_id}/export")
def export_model(model_id: str) -> FileResponse:
    s = get_session()
    try:
        m = reg.get_model_or_404(s, model_id)
        path = reg._abs(m.path)
        if not path.is_file():
            raise AppError(
                "MODEL_NOT_FOUND", "模型文件在数据目录中缺失",
                "请重新导入该模型，或检查数据目录后重试", 404,
            )
        return FileResponse(path, filename=path.name, media_type="application/octet-stream")
    finally:
        s.close()


@router.post("/{model_id}/convert", status_code=202)
def convert_model(model_id: str) -> dict:
    """触发 `.pth` / `.safetensors` → `.onnx` 的离线转换（**异步**，立即返回作业号）。

    转换在独立环境里以子进程执行（ADR-003：应用内不含 torch），导出实测数十秒，
    同步等待会顶穿前端超时——故立即返回，用 `GET` 取状态。
    """
    s = get_session()
    try:
        m = reg.get_model_or_404(s, model_id)
        if m.format not in conversion_service.CONVERTIBLE_FORMATS:
            raise AppError(
                "MODEL_NOT_CONVERTIBLE", f"{m.format} 格式无需转换",
                "转换入口只对 .pth / .safetensors 开放；其它格式直接加载或导入即可", 400,
            )
        result = conversion_service.trigger(m.id)
        if not result.get("started") and result.get("code") == "convert_env_missing":
            raise AppError(
                "CONVERT_ENV_MISSING", "转换环境未安装，无法执行离线转换",
                result.get("reason") or "", 409,
            )
        return result
    finally:
        s.close()


@router.get("/{model_id}/convert")
def convert_status(model_id: str) -> dict:
    """转换作业状态与结果（与触发同一路径，`GET` 取状态）。

    返回 `{status, job_id, model_id, started_at, finished_at, result, error, availability}`。
    `result.model_id` 是**转换产物**登记成的新模型 id（与源模型 id 不同）。
    """
    s = get_session()
    try:
        m = reg.get_model_or_404(s, model_id)
        state = conversion_service.serialize_state()
        return {
            **state,
            "source_model_id": f"mdl_{m.id}",
            "availability": conversion_service.describe_availability(),
        }
    finally:
        s.close()


@router.delete("/{model_id}", status_code=204)
def delete_model(model_id: str) -> Response:
    s = get_session()
    try:
        reg.delete_model(s, model_id)
        return Response(status_code=204)
    finally:
        s.close()
