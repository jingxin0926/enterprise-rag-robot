"""
全局异常处理器（类似 Spring 的 @ControllerAdvice）

统一拦截 4 类异常并返回标准 R 结构：
1. BizException        业务异常（已知错误码）
2. RequestValidationError  参数校验失败（Pydantic）
3. HTTPException       FastAPI 内置 HTTP 异常
4. Exception           兜底未知异常
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import ORJSONResponse
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import BizException, ErrorCode
from app.core.response import R
from app.middleware.trace import get_trace_id


def register_exception_handlers(app: FastAPI) -> None:
    """注册所有全局异常处理器"""

    # ---------- 1. 业务异常 ----------
    @app.exception_handler(BizException)
    async def biz_exception_handler(request: Request, exc: BizException) -> ORJSONResponse:
        trace_id = get_trace_id()
        logger.warning(
            "[BizException] path={} code={} message={} trace_id={}",
            request.url.path,
            exc.code,
            exc.message,
            trace_id,
        )
        return ORJSONResponse(
            status_code=200,  # 业务异常仍走 200，由 code 区分（团队统一）
            content=R.fail(
                code=exc.code,
                message=exc.message,
                data=exc.data,
                trace_id=trace_id,
            ).model_dump(),
        )

    # ---------- 2. 参数校验异常 ----------
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> ORJSONResponse:
        trace_id = get_trace_id()
        # 将 Pydantic 错误转为人类可读
        errors = []
        for err in exc.errors():
            loc = ".".join(str(x) for x in err.get("loc", []))
            errors.append(f"{loc}: {err.get('msg')}")
        message = "; ".join(errors) if errors else "参数校验失败"

        logger.warning(
            "[ValidationError] path={} message={} trace_id={}",
            request.url.path,
            message,
            trace_id,
        )
        return ORJSONResponse(
            status_code=200,
            content=R.fail(
                code=ErrorCode.PARAM_INVALID,
                message=message,
                data=errors,
                trace_id=trace_id,
            ).model_dump(),
        )

    # ---------- 3. HTTP 异常 ----------
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> ORJSONResponse:
        trace_id = get_trace_id()
        logger.warning(
            "[HTTPException] path={} status={} detail={} trace_id={}",
            request.url.path,
            exc.status_code,
            exc.detail,
            trace_id,
        )
        return ORJSONResponse(
            status_code=exc.status_code,
            content=R.fail(
                code=exc.status_code,
                message=str(exc.detail),
                trace_id=trace_id,
            ).model_dump(),
        )

    # ---------- 4. 未知异常兜底 ----------
    @app.exception_handler(Exception)
    async def unknown_exception_handler(request: Request, exc: Exception) -> ORJSONResponse:
        trace_id = get_trace_id()
        logger.exception(
            "[UnknownException] path={} trace_id={} | {}",
            request.url.path,
            trace_id,
            exc,
        )
        return ORJSONResponse(
            status_code=500,
            content=R.fail(
                code=ErrorCode.INTERNAL_ERROR,
                message="服务器内部错误，请联系管理员",
                trace_id=trace_id,
            ).model_dump(),
        )
