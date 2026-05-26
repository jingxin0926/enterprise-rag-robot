"""
FastAPI 应用入口

启动方式：
    uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from loguru import logger

from app.api.v1 import api_router_v1
from app.core.config import settings
from app.core.exception_handler import register_exception_handlers
from app.core.logger import setup_logger
from app.middleware.trace import TraceMiddleware


# ================================================================
# 应用生命周期（启动 / 关闭钩子）
# ================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理：
    - 启动时：初始化日志、连接池、注册外部资源等
    - 关闭时：优雅释放资源
    """
    # ---------- 启动阶段 ----------
    setup_logger()
    logger.info("🚀 应用启动中... env={} version={}", settings.app_env.value, settings.app_version)

    # 初始化 LLM 客户端
    from app.infra.llm.deepseek_client import get_deepseek_client
    get_deepseek_client()

    # 初始化 Redis（允许失败，降级到内存）
    from app.infra.cache.redis_client import get_redis
    await get_redis()

    # TODO P2: 初始化向量库连接

    logger.info("✅ 应用启动完成，监听端口 {}", settings.app_port)

    yield  # 应用运行中

    # ---------- 关闭阶段 ----------
    logger.info("🛑 应用关闭中...")
    from app.infra.llm.deepseek_client import deepseek_client
    if deepseek_client:
        await deepseek_client.close()
    from app.infra.cache.redis_client import close_redis
    await close_redis()
    logger.info("👋 应用已优雅关闭")


# ================================================================
# 创建 FastAPI 应用实例
# ================================================================
def create_app() -> FastAPI:
    """工厂方法创建应用实例（便于测试时复用）"""
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="智能问答系统 - DeepSeek + RAG + Agent",
        # 仅开发环境暴露 docs，生产环境关闭
        docs_url="/docs" if settings.is_dev else None,
        redoc_url="/redoc" if settings.is_dev else None,
        openapi_url="/openapi.json" if settings.is_dev else None,
        lifespan=lifespan,
        # 默认使用 orjson，性能比标准 json 高 5-10 倍
        default_response_class=ORJSONResponse,
    )

    # ---------- 中间件（顺序：后注册的先执行） ----------
    # CORS（开发阶段全开，生产收紧）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.is_dev else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 多租户识别（从 JWT 提取 tenant_id 注入上下文）
    from app.middleware.tenant import TenantMiddleware
    app.add_middleware(TenantMiddleware)

    # 链路追踪（最先注册 -> 最先执行 -> 全程贯穿）
    app.add_middleware(TraceMiddleware)

    # ---------- 全局异常处理 ----------
    register_exception_handlers(app)

    # ---------- 限流 ----------
    from app.middleware.rate_limit import limiter
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # ---------- 路由注册 ----------
    app.include_router(api_router_v1)

    return app


# 全局 app 实例（uvicorn 启动时通过 app.main:app 引用）
app = create_app()
