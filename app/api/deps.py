"""
API 依赖注入

提供公共的依赖：认证、租户、限流等
"""

from fastapi import Depends, Header, Request
from loguru import logger

from app.core.exceptions import UnauthorizedException, BizException, ErrorCode
from app.core.security import TokenPayload, verify_token


async def get_current_user(
    authorization: str = Header(default="", alias="Authorization"),
) -> TokenPayload:
    """
    获取当前认证用户（从 Authorization header 解析 JWT）

    用法（在路由中）：
        @router.get("/profile")
        async def profile(user: TokenPayload = Depends(get_current_user)):
            return user.username

    """
    if not authorization:
        raise UnauthorizedException("请提供认证 Token（Header: Authorization: Bearer <token>）")

    # 解析 Bearer token
    parts = authorization.split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise UnauthorizedException("Token 格式错误，应为: Bearer <token>")

    token = parts[1]
    payload = verify_token(token)
    if payload is None:
        raise UnauthorizedException("Token 无效或已过期，请重新登录")

    return payload


async def get_optional_user(
    authorization: str = Header(default="", alias="Authorization"),
) -> TokenPayload | None:
    """
    可选认证（未登录返回 None，不报错）

    适用于：登录和未登录都能用的接口，但登录后有额外功能
    """
    if not authorization:
        return None

    parts = authorization.split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None

    return verify_token(parts[1])


async def get_tenant_id(
    user: TokenPayload = Depends(get_current_user),
) -> str:
    """
    获取当前租户 ID

    多租户隔离的关键：从 token 中提取 tenant_id
    """
    return user.tenant_id
