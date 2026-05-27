"""
认证接口

功能：
1. POST /api/v1/auth/login  — 登录获取 Token
2. GET  /api/v1/auth/me     — 获取当前用户信息

说明：
P5 阶段用内存模拟用户数据（无数据库），生产接 MySQL 即可。
核心是验证 JWT 流程跑通。
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
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
# 用户数据（当前内存存储，生产环境接 MySQL）
# ============================================================
MOCK_USERS = {
    "admin": {
        "user_id": "u_001",
        "username": "admin",
        "password_hash": hash_password("admin123"),
        "tenant_id": "t_default",
        "role": "admin",
    },
    "user1": {
        "user_id": "u_002",
        "username": "user1",
        "password_hash": hash_password("user123"),
        "tenant_id": "t_default",
        "role": "user",
    },
    "demo": {
        "user_id": "u_003",
        "username": "demo",
        "password_hash": hash_password("demo123"),
        "tenant_id": "t_demo",  # 不同租户
        "role": "user",
    },
}


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

    测试账号：
    - admin / admin123（管理员，租户 t_default）
    - user1 / user123（普通用户，租户 t_default）
    - demo / demo123（用户，租户 t_demo）
    """
    user = MOCK_USERS.get(req.username)
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
