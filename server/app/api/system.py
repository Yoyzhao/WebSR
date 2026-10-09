"""系统路由（api-contract §4.4）。

- `GET /api/system/capabilities`：形状与语义由 system_info 保证；数据为保底
  占位（T-803 接入真实探测与 EP 验证后原位替换）。
- `GET /api/system/diagnostics`：诊断 JSON 附件下载（PRD §3.4）。
- `POST /api/system/calibrate` / `GET /api/system/calibration` 属 S2（自标定），
  本阶段不实现。
"""
import json

from fastapi import APIRouter
from fastapi.responses import Response

from ..services.system_info import build_capabilities, build_diagnostics

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/capabilities")
def capabilities() -> dict:
    return build_capabilities()


@router.get("/diagnostics")
def diagnostics() -> Response:
    payload = json.dumps(build_diagnostics(), ensure_ascii=False, indent=2)
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="websr-diagnostics.json"'},
    )
