"""统一响应体与全路由兜底。

约定：所有接口都返回 ``{"code": ..., "data": ..., "msg": ...}``

- 成功：``code=200``、``data`` 放业务数据、``msg="ok"``（HTTP 状态保留 200/201 等真实语义）
- 失败：``code=422``、``data`` 放明细（没有就 null）、``msg`` 放错误提示（HTTP 状态同步 422）
- 未预期异常：``code=500``、``msg`` 通用提示（HTTP 500，堆栈只进服务端日志）

兜底入口只有一个：``EnvelopeRoute``。所有业务路由都挂这个路由类，
因此不需要在每个接口里重复写 try/except —— 写法等价，且不会漏掉任何一个端点。
"""

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("app.response")

SUCCESS_CODE = 200
FAILURE_CODE = 422
SERVER_ERROR_CODE = 500
SERVER_ERROR_MSG = "服务器内部错误，请稍后重试"

#: 业务失败时返回的 HTTP 状态：统一 200，失败与否只看 body 里的 code。
#: 想让 HTTP 状态也表达失败，把它改成 422 即可（error_response 会跟着变）。
FAILURE_HTTP_STATUS = 200


def success_body(data: Any = None, msg: str = "ok") -> dict[str, Any]:
    return {"code": SUCCESS_CODE, "data": data, "msg": msg}


def failure_body(msg: str, *, code: int = FAILURE_CODE, data: Any = None) -> dict[str, Any]:
    return {"code": code, "data": data, "msg": msg}


def error_response(exc: Exception) -> JSONResponse:
    """把任何异常翻译成统一的失败结构。"""
    # 延迟导入：exceptions.py 需要引用本模块的 error_response，避免循环导入
    from app.core.exceptions import AppError

    if isinstance(exc, RequestValidationError):
        return JSONResponse(
            status_code=FAILURE_HTTP_STATUS,
            content=failure_body("请求参数校验失败", data=jsonable_encoder(exc.errors())),
        )
    if isinstance(exc, AppError):
        # 业务异常（未找到 / 冲突 / 规则不满足）统一 422，具体原因放 msg
        return JSONResponse(
            status_code=FAILURE_HTTP_STATUS, content=failure_body(exc.message, data=exc.detail)
        )
    if isinstance(exc, StarletteHTTPException):
        return JSONResponse(status_code=FAILURE_HTTP_STATUS, content=failure_body(str(exc.detail)))
    logger.exception("未处理的异常：%s", exc)
    return JSONResponse(
        status_code=SERVER_ERROR_CODE,
        content=failure_body(SERVER_ERROR_MSG, code=SERVER_ERROR_CODE),
    )


def wrap_success(response: Response) -> Response:
    """成功响应套上统一结构；文件流等非 JSON 响应原样返回。

    这里按 Content-Type 判断而不是 isinstance(JSONResponse)：FastAPI 对带返回注解的接口
    会给出普通 Response，只看类型会漏包。
    """
    if not response.headers.get("content-type", "").startswith("application/json"):
        return response
    body = getattr(response, "body", None)  # 流式响应没有 body，直接放过
    if body is None:
        return response
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return response
    if isinstance(payload, dict) and {"code", "data", "msg"} <= payload.keys():
        return response
    return JSONResponse(
        content=success_body(payload),
        status_code=response.status_code,
        background=response.background,
    )


class EnvelopeRoute(APIRoute):
    """统一响应体的路由类：异常兜底 + 成功包壳。"""

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                response = await original(request)
            except Exception as exc:  # 兜底：所有业务异常都从这里出去
                return error_response(exc)
            return wrap_success(response)

        return handler


__all__ = [
    "EnvelopeRoute",
    "FAILURE_CODE",
    "SERVER_ERROR_CODE",
    "SERVER_ERROR_MSG",
    "SUCCESS_CODE",
    "error_response",
    "failure_body",
    "success_body",
    "wrap_success",
]
