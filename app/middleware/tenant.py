"""
多租户中间件

职责：
- 从请求的 JWT token 中提取 tenant_id
- 注入到请求上下文（ContextVar）
- 后续所有业务层通过 get_current_tenant() 获取，无需显式传参

工作流程：
    请求进入 → 解析 JWT → 提取 tenant_id → 存入 ContextVar
    → 业务代码任意位置调用 get_current_tenant() 获取
    → 请求结束，ContextVar 自动回收
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.security import verify_token
from app.core.tenant import set_current_tenant


class TenantMiddleware(BaseHTTPMiddleware):
    """
    租户识别中间件

    从 Authorization header 的 JWT 中提取 tenant_id。
    未认证请求默认为 "default" 租户（公共资源）。
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        tenant_id = "default"

        # 尝试从 JWT 中提取 tenant_id
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            try:
                payload = verify_token(token)
                if payload and payload.tenant_id:
                    tenant_id = payload.tenant_id
            except Exception:
                pass  # token 无效时降级为 default 租户

        # 设置当前请求的租户上下文
        set_current_tenant(tenant_id)

        response = await call_next(request)
        return response
