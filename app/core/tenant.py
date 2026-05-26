"""
多租户上下文管理

设计思路：
- 每个请求通过 JWT token 识别租户（tenant_id）
- tenant_id 贯穿整个请求生命周期，各层通过 get_current_tenant() 获取
- 数据层根据 tenant_id 做隔离（知识库、会话、Prompt）

隔离维度：
1. 知识库隔离：每个租户独立的 Qdrant collection（tenant_{id}_knowledge）
2. BM25 索引隔离：按 tenant_id 独立维护
3. 会话记忆隔离：Redis key 带 tenant_id 前缀
4. Prompt 隔离：支持租户级定制，fallback 到默认
5. Token 计费隔离：按 tenant_id 独立统计

使用方式：
    # 中间件/依赖注入中设置
    set_current_tenant("tenant_001")

    # 业务层任意位置获取
    tenant_id = get_current_tenant()
"""

from contextvars import ContextVar

# 用 ContextVar 存储当前请求的租户ID（协程安全，每个请求独立）
_current_tenant: ContextVar[str] = ContextVar("current_tenant", default="default")


def set_current_tenant(tenant_id: str) -> None:
    """
    设置当前请求的租户ID

    在中间件或依赖注入中调用，之后整个请求链路都能获取到
    """
    _current_tenant.set(tenant_id)


def get_current_tenant() -> str:
    """
    获取当前请求的租户ID

    任何业务层代码都可以调用，无需显式传参
    """
    return _current_tenant.get()


def get_tenant_collection_name(tenant_id: str | None = None) -> str:
    """
    获取租户对应的 Qdrant collection 名称

    命名规则：tenant_{id}_knowledge
    默认租户使用 "knowledge_base"（兼容现有数据）
    """
    tid = tenant_id or get_current_tenant()
    if tid == "default":
        return "knowledge_base"
    return f"tenant_{tid}_knowledge"


def get_tenant_cache_prefix(tenant_id: str | None = None) -> str:
    """
    获取租户对应的 Redis key 前缀

    用于会话记忆、缓存等 Redis 操作的 key 隔离
    """
    tid = tenant_id or get_current_tenant()
    return f"t:{tid}"
