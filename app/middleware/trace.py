"""
链路追踪中间件

设计要点：
1. 为每个请求分配唯一 trace_id（或沿用上游传入的 X-Trace-Id）
2. 将 trace_id 注入 contextvars，业务代码 / 日志可通过 logger 自动带出
3. 同时记录请求耗时与状态码，便于性能分析
"""

import time
import uuid
from contextvars import ContextVar

from fastapi import Request
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

# 使用 ContextVar 存储 trace_id，天然支持异步并发
_trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="")

TRACE_HEADER = "X-Trace-Id"


def get_trace_id() -> str:
    """获取当前请求的 trace_id"""
    return _trace_id_ctx.get()


def _gen_trace_id() -> str:
    """生成 trace_id（去掉 uuid 中的 - 节省字符）"""
    return uuid.uuid4().hex


class TraceMiddleware(BaseHTTPMiddleware):
    """链路追踪 + 访问日志中间件"""

    async def dispatch(self, request: Request, call_next) -> Response:
        # 1. 取或生成 trace_id
        trace_id = request.headers.get(TRACE_HEADER) or _gen_trace_id()
        token = _trace_id_ctx.set(trace_id)

        start = time.perf_counter()
        method = request.method
        path = request.url.path

        try:
            # 2. 通过 loguru 的 contextualize 把 trace_id 自动绑到本次日志
            with logger.contextualize(trace_id=trace_id):
                response: Response = await call_next(request)
            cost_ms = (time.perf_counter() - start) * 1000
            response.headers[TRACE_HEADER] = trace_id
            logger.info(
                "[ACCESS] {} {} -> {} | {:.2f}ms | trace_id={}",
                method,
                path,
                response.status_code,
                cost_ms,
                trace_id,
            )
            return response
        except Exception as e:
            cost_ms = (time.perf_counter() - start) * 1000
            logger.exception(
                "[ACCESS] {} {} -> ERROR | {:.2f}ms | trace_id={} | {}",
                method,
                path,
                cost_ms,
                trace_id,
                e,
            )
            raise
        finally:
            _trace_id_ctx.reset(token)
