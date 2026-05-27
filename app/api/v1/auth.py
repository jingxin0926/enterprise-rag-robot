"""
认证接口

功能：
1. POST /api/v1/auth/login  — 登录获取 Token
2. GET  /api/v1/auth/me     — 获取当前用户信息

说明：
当前用内存模拟用户数据（无数据库），后续接 MySQL 用户表即可。
初始管理员密码从环境变量 ADMIN_INIT_PASSWORD 读取，避免硬编码。
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.exceptions import BizException, ErrorCode
from app.core.response import R
from app.core.security import (
    ACCESS_TOKEN_EXPIRE_HOURS,
    TokenPayload,
    TokenResponse,
    create_access_token,
    hash_password,
    verify_password,
)
from app.middleware.trace import get_trace_id

router = APIRouter(prefix="/auth", tags=["认证"])


# ============================================================
# 用户数据（内存存储，生产环境接 MySQL）
# 初始密码从环境变量读取，不在代码中硬编码
# ============================================================
def _build_users() -> dict[str, dict]:
    """
    构建用户表

    admin 密码来源：环境变量 ADMIN_INIT_PASSWORD（必须配置）
    生产环境如果漏配，启动时 fail-fast 拦截（见 config.py）
    """
    admin_pwd = settings.admin_init_password
    return {
        "admin": {
            "user_id": "u_001",
            "username": "admin",
            "password_hash": hash_password(admin_pwd),
            "tenant_id": "t_default",
            "role": "admin",
        },
    }


# 延迟初始化（等 settings 加载完毕）
_users: dict[str, dict] | None = None


def _get_users() -> dict[str, dict]:
    """获取用户表（懒加载单例）"""
    global _users
    if _users is None:
        _users = _build_users()
    return _users


# ============================================================
# 请求/响应模型
# ============================================================
class LoginRequest(BaseModel):
    """登录请求"""

    username: str = Field(..., min_length=1, description="用户名")
    password: str = Field(..., min_length=1, description="密码")


# ============================================================
# 接口
# ============================================================
@router.post("/login", summary="登录")
async def login(req: LoginRequest):
    """
    用户登录，返回 JWT Token
    """
    users = _get_users()
    user = users.get(req.username)
    if not user:
        raise BizException(ErrorCode.UNAUTHORIZED, "用户名或密码错误")

    if not verify_password(req.password, user["password_hash"]):
        raise BizException(ErrorCode.UNAUTHORIZED, "用户名或密码错误")

    # 生成 Token
    token = create_access_token(
        user_id=user["user_id"],
        username=user["username"],
        tenant_id=user["tenant_id"],
        role=user["role"],
    )

    return R.success(
        data=TokenResponse(
            access_token=token,
            expires_in=ACCESS_TOKEN_EXPIRE_HOURS * 3600,
            user_id=user["user_id"],
            tenant_id=user["tenant_id"],
        ).model_dump(),
        message="登录成功",
        trace_id=get_trace_id(),
    )


@router.get("/me", summary="当前用户信息")
async def get_me(user: TokenPayload = Depends(get_current_user)):
    """获取当前登录用户信息（需要认证）"""
    return R.success(
        data={
            "user_id": user.user_id,
            "username": user.username,
            "tenant_id": user.tenant_id,
            "role": user.role,
        },
        trace_id=get_trace_id(),
    )
