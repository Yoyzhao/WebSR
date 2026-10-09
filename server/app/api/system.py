"""系统路由（api-contract §4.4）。

- `GET  /api/system/capabilities`：硬件事实 / 档位 / 已采纳后端（阶段 A/B）。
- `POST /api/system/calibrate`：触发首启自标定（**异步**，立即返回作业号）。
- `GET  /api/system/calibration`：标定记录、推荐档位与理由。
- `GET  /api/system/diagnostics`：诊断 JSON 附件下载（PRD §3.4）。

标定端点属 **S2**（T-805）。`POST /calibrate` 触发的是**后台线程**，接口本身不阻塞——
标定要吃满 GPU 数秒，同步等待会顶穿前端超时。
"""
import json

from fastapi import APIRouter
from fastapi.responses import Response

from ..services import calibration_service
from ..services.system_info import build_capabilities, build_diagnostics

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/capabilities")
def capabilities() -> dict:
    return build_capabilities()


@router.post("/calibrate")
def calibrate() -> dict:
    """触发一轮标定（异步）。已有作业在跑时返回当前状态而不是重复触发。"""
    return calibration_service.trigger(reason="manual")


@router.get("/calibration")
def calibration() -> dict:
    return calibration_service.describe()


@router.get("/diagnostics")
def diagnostics() -> Response:
    payload = json.dumps(build_diagnostics(), ensure_ascii=False, indent=2)
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="websr-diagnostics.json"'},
    )
