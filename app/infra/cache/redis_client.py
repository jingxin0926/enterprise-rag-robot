"""
Redis 客户端

设计要点：
1. 使用 redis-py 异步客户端 + hiredis 加速解析
2. 连接池化，生产环境复用连接
3. 优雅降级：Redis 不可用时，内存 fallback（开发阶段 Redis 非必需）
"""

import redis.asyncio as aioredis
from loguru import logger

from app.core.config import settings

# 全局 Redis 连接池
_redis_pool: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis | None:
    """
    获取 Redis 连接

    如果连接失败，返回 None（开发时可不装 Redis）
    """
    global _redis_pool
    if _redis_pool is not None:
        return _redis_pool

    try:
        _redis_pool = aioredis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password or None,
            db=settings.redis_db,
            decode_responses=True,  # 自动解码为 str
            socket_connect_timeout=3,  # 连接超时 3 秒
            socket_timeout=5,
        )
        # 测试连通性
        await _redis_pool.ping()
        logger.info("✅ Redis 连接成功 | {}:{}/{}", settings.redis_host, settings.redis_port, settings.redis_db)
        return _redis_pool
    except Exception as e:
        logger.warning("⚠️ Redis 连接失败，将使用内存存储 | error={}", e)
        _redis_pool = None
        return None


async def close_redis() -> None:
    """关闭 Redis 连接"""
    global _redis_pool
    if _redis_pool:
        await _redis_pool.close()
        _redis_pool = None
        logger.info("Redis 连接已关闭")
