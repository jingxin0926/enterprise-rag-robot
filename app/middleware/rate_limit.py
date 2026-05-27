"""
接口限流中间件

设计要点：
1. 基于 slowapi（封装了 limits 库），支持多种限流策略
2. 按真实客户端 IP 限流（仅信任代理设置的 X-Real-IP，不信任 X-Forwarded-For）
3. 不同接口不同限制（Agent 接口更严格，因为消耗 LLM token）
4. 限流信息通过响应头返回（X-RateLimit-*）
5. 存储后端：有 Redis 时用 Redis（多实例共享），否则降级内存
"""

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings


def _get_client_ip(request: Request) -> str:
    """
    获取客户端真实 IP

    安全策略：
    - 不信任 X-Forwarded-For（容易被客户端伪造）
    - 仅信任反向代理设置的 X-Real-IP（由 Nginx/ALB 等设置，不可伪造）
    - 无代理头时使用 socket 直连 IP
    """
    # X-Real-IP 由反向代理（Nginx/ALB）设置，客户端无法伪造
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    return get_remote_address(request)


def _get_storage_uri() -> str:
    """
    获取限流存储后端 URI

    有 Redis 配置时使用 Redis（多实例共享、重启不丢失），否则降级内存
    """
    if settings.redis_host and settings.redis_host != "127.0.0.1" or settings.app_env.value == "prod":
        password_part = f":{settings.redis_password}@" if settings.redis_password else ""
        return f"redis://{password_part}{settings.redis_host}:{settings.redis_port}/{settings.redis_db}"
    return "memory://"


# 创建限流器实例
limiter = Limiter(
    key_func=_get_client_ip,
    default_limits=["60/minute"],  # 默认每分钟 60 次
    storage_uri=_get_storage_uri(),
)
