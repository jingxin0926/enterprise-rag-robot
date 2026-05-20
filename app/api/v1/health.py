"""
健康检查接口

用途：
1. 部署后自检
2. K8s liveness / readiness 探针
3. 网关健康检查
"""

from fastapi import APIRouter

from app.core.config import settings
from app.core.response import R
from app.middleware.trace import get_trace_id

router = APIRouter(prefix="/health", tags=["健康检查"])


@router.get("", summary="健康检查")
async def health() -> R:
    """
    返回应用基础信息

    后续 P5 会扩展为深度健康检查：探测 DB / Redis / Qdrant / DeepSeek 联通性
    """
    return R.success(
        data={
            "name": settings.app_name,
            "version": settings.app_version,
            "env": settings.app_env.value,
            "status": "UP",
        },
        trace_id=get_trace_id(),
    )


@router.get("/ping", summary="探活")
async def ping() -> R:
    """最简单的探活接口，K8s liveness 用"""
    return R.success(data="pong", trace_id=get_trace_id())
