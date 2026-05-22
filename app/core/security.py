"""
安全模块：JWT 鉴权 + 密码加密

设计要点：
1. JWT 无状态认证（类似 Spring Security + JWT）
2. 支持多租户：token 中携带 tenant_id
3. 密码使用 bcrypt 加密存储
4. Token 过期自动失效
"""

from datetime import datetime, timedelta, timezone

import jwt
from loguru import logger
from passlib.context import CryptContext
from pydantic import BaseModel

from app.core.config import settings

# ============================================================
# 配置常量
# ============================================================
# JWT 密钥（生产环境从环境变量读取）
JWT_SECRET_KEY = "enterprise-rag-robot-secret-key-change-in-production"
JWT_ALGORITHM = "HS256"
# Token 过期时间（小时）
ACCESS_TOKEN_EXPIRE_HOURS = 24

# 密码加密上下文
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ============================================================
# 数据模型
# ============================================================
class TokenPayload(BaseModel):
    """JWT Payload 结构"""

    user_id: str
    username: str
    tenant_id: str  # 租户 ID（多租户隔离的关键）
    role: str = "user"  # user / admin
    exp: datetime | None = None


class TokenResponse(BaseModel):
    """登录成功返回的 Token"""

    access_token: str
    token_type: str = "bearer"
    expires_in: int  # 秒
    user_id: str
    tenant_id: str


# ============================================================
# JWT 工具函数
# ============================================================
def create_access_token(
    user_id: str,
    username: str,
    tenant_id: str,
    role: str = "user",
    expires_hours: int = ACCESS_TOKEN_EXPIRE_HOURS,
) -> str:
    """
    创建 JWT Token

    Args:
        user_id: 用户 ID
        username: 用户名
        tenant_id: 租户 ID
        role: 角色
        expires_hours: 过期小时数

    Returns:
        JWT token 字符串
    """
    expire = datetime.now(timezone.utc) + timedelta(hours=expires_hours)
    payload = {
        "user_id": user_id,
        "username": username,
        "tenant_id": tenant_id,
        "role": role,
        "exp": expire,
    }
    token = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return token


def verify_token(token: str) -> TokenPayload | None:
    """
    验证并解析 JWT Token

    Returns:
        TokenPayload 或 None（验证失败时）
    """
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return TokenPayload(**payload)
    except jwt.ExpiredSignatureError:
        logger.warning("[Auth] Token 已过期")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning("[Auth] Token 无效: {}", e)
        return None


# ============================================================
# 密码工具
# ============================================================
def hash_password(password: str) -> str:
    """密码哈希（bcrypt）"""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    return pwd_context.verify(plain_password, hashed_password)
