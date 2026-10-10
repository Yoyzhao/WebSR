"""统一错误体与全局异常处理（api-contract.md §2.2）。

三要素缺一不可：code（机器可读）+ message（用户可读）+ suggestion（下一步该做什么）；
detail 可选（收进前端「查看日志」详情，不在正文浮现）。
已定稿 16 个错误码见 api-contract.md §2.3 ↔ web/src/api/errorMessages.ts，
后端文案与前端对照表保持一致（前端按 code 取自有文案，后端 message 是未知码兜底时的展示源）。
"""
import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("websr.errors")


class AppError(Exception):
    """业务错误基类：抛出即按统一错误体返回。

    各业务模块（T-604 起）以此构造具体错误，禁止使用裸 HTTPException 绕过统一错误体。
    """

    def __init__(
        self,
        code: str,
        message: str,
        suggestion: str,
        status_code: int = 400,
        detail: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.suggestion = suggestion
        self.status_code = status_code
        self.detail = detail


def _body(code: str, message: str, suggestion: str, detail: dict | None = None) -> dict:
    error: dict = {"code": code, "message": message, "suggestion": suggestion}
    if detail is not None:
        error["detail"] = detail
    return {"error": error}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(exc.code, exc.message, exc.suggestion, exc.detail),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # 契约 §2.3：VALIDATION_ERROR（400）→ 前端表单内联错误
        return JSONResponse(
            status_code=400,
            content=_body(
                "VALIDATION_ERROR",
                "请求参数不完整或格式不正确",
                "请检查表单中的必填项后重试",
                detail={"errors": jsonable_encoder(exc.errors())},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # 框架级异常（未匹配路由 404 / 方法不允许 405 等）。
        # 404 → NOT_FOUND；405 → METHOD_NOT_ALLOWED；413 → FILE_TOO_LARGE；
        # 其余框架状态归 INTERNAL_ERROR 语义位（契约 §2.3 全 20 条，T-700 已定稿）。
        if exc.status_code == 404:
            code, message, suggestion = "NOT_FOUND", "资源不存在", "请确认访问地址或刷新列表后重试"
        elif exc.status_code == 405:
            code, message, suggestion = (
                "METHOD_NOT_ALLOWED", "该地址不支持此请求方法", "请检查请求方法后重试",
            )
        elif exc.status_code == 413:
            code, message, suggestion = "FILE_TOO_LARGE", "文件超过大小上限", "请压缩图片或降低分辨率后重试"
        else:
            code, message, suggestion = "INTERNAL_ERROR", "服务端发生未预期错误", "请导出诊断 JSON 并查看日志定位原因"
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(code, message, suggestion),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # 未预期异常必须落盘日志（api-contract §2.3 INTERNAL_ERROR 触发场景）
        logger.exception("未预期异常: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=_body(
                "INTERNAL_ERROR",
                "服务端发生未预期错误",
                "请导出诊断 JSON 并查看日志定位原因",
            ),
        )
