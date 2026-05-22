"""
接口限流中间件

设计要点：
1. 基于 slowapi（封装了 limits 库），支持多种限流策略
2. 按 IP / 按用户 限流
3. 不同接口不同限制（Agent 接口更严格，因为消耗 LLM token）
4. 限流信息通过响应头返回（X-RateLimit-*）
"""

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


def _get_client_ip(request: Request) -> str:
    """获取客户端 IP（支持代理场景）"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return get_remote_address(request)


# 创建限流器实例
limiter = Limiter(
    key_func=_get_client_ip,
    default_limits=["60/minute"],  # 默认每分钟 60 次
    storage_uri="memory://",  # 内存存储（生产用 Redis）
)
