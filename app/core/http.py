"""注册全局 HTTP 中间件和异常到 JSON 响应的映射规则。"""

import logging
import time
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import AppError
from app.schemas.responses import response_body

logger = logging.getLogger("oj.http")


def register_http_conventions(app: FastAPI) -> None:
    """为应用安装请求日志与统一异常处理。

    中间件包围一次完整请求；异常处理器把不同来源的错误转换为课程
    契约要求的 ``{code, msg, data}``，同时保留真实 HTTP 状态码。
    """

    @app.middleware("http")
    async def request_log_middleware(request: Request, call_next):
        """记录不含请求体和认证信息的最小访问日志。"""

        # 每次请求生成独立 ID，客户端可用响应头中的 ID 协助定位日志。
        request_id = uuid4().hex
        started_at = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        logger.info(
            "request_id=%s method=%s path=%s status=%s elapsed_ms=%.2f",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        """将服务层主动抛出的安全业务错误原样映射为 HTTP 响应。"""

        return JSONResponse(
            status_code=exc.status_code,
            content=response_body(exc.status_code, exc.message, exc.data),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, _exc: RequestValidationError
    ) -> JSONResponse:
        """将 FastAPI 默认的 422 参数校验错误统一转换为 400。"""

        return JSONResponse(
            status_code=400,
            content=response_body(400, "invalid request"),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """统一框架产生的 404 等 HTTP 错误，并过滤非字符串详情。"""

        message = exc.detail if isinstance(exc.detail, str) else "request failed"
        return JSONResponse(
            status_code=exc.status_code,
            content=response_body(exc.status_code, message),
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        """兜底处理未知异常，不向客户端或日志写入异常原文。"""

        # 异常消息可能包含路径、密钥或用户代码，因此这里只记录类型。
        logger.error("unhandled_exception type=%s", type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content=response_body(500, "internal server error"),
        )
