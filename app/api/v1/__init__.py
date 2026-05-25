"""
API v1 路由聚合

将各模块的 router 统一注册到 api_router_v1，
由 main.py 一次性挂载到应用上。
"""

from fastapi import APIRouter

from app.api.v1.health import router as health_router
from app.api.v1.auth import router as auth_router
from app.api.v1.chat import router as chat_router
from app.api.v1.knowledge import router as knowledge_router
from app.api.v1.agent import router as agent_router

api_router_v1 = APIRouter(prefix="/api/v1")

api_router_v1.include_router(health_router)
api_router_v1.include_router(auth_router)
api_router_v1.include_router(chat_router)
api_router_v1.include_router(knowledge_router)
api_router_v1.include_router(agent_router)
