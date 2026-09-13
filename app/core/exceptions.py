"""业务异常与全局异常处理。

业务异常只表达"为什么失败"，最终响应结构由 app/core/response.py 统一成
``{code: 422, data: 明细, msg: 提示}``。
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.response import error_response


class AppError(Exception):
    """业务异常基类，统一转成 {code:422, data, msg} 结构。"""

    code: str = "APP_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.detail = detail


class NotFoundError(AppError):
    """资源不存在（对外同样是 code=422，msg 说明找不到什么）。"""

    code = "NOT_FOUND"


class ConflictError(AppError):
    """唯一约束 / 状态冲突。"""

    code = "CONFLICT"


class BusinessRuleError(AppError):
    """业务规则不满足（如技能依赖成环、名单校验不通过）。"""

    code = "BUSINESS_RULE_VIOLATION"


def register_exception_handlers(app: FastAPI) -> None:
    """第二道网：异常发生在路由之外（中间件 / 依赖注入阶段）时也返回统一结构。

    路由内部的兜底在 EnvelopeRoute 里，两者共用 error_response，保证结构一致。
    """

    @app.exception_handler(AppError)
    async def _app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return error_response(exc)

    @app.exception_handler(StarletteHTTPException)
    async def _http_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(exc)

    @app.exception_handler(Exception)
    async def _unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
        return error_response(exc)
