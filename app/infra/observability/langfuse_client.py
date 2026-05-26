"""
Langfuse 可观测性客户端

功能：
1. 追踪所有 LLM 调用（输入、输出、耗时、token 消耗）
2. 记录完整的 RAG 链路（检索 → 生成 → 评测）
3. 监控 Agent 工具调用路径
4. 统计成本（按租户、按接口）

Langfuse 是什么：
- 开源的 LLM 可观测性平台
- 类似 APM（Application Performance Monitoring）但专门给 AI 应用设计
- 能看到每次 LLM 调用的完整上下文：Prompt、输入、输出、耗时、token

配置方式：
    在 .env 中配置：
    LANGFUSE_PUBLIC_KEY=pk-xxx
    LANGFUSE_SECRET_KEY=sk-xxx
    LANGFUSE_HOST=https://cloud.langfuse.com  (或自建地址)

    不配置则自动降级为无追踪模式（不影响业务功能）
"""

import os
from functools import lru_cache

from loguru import logger

# Langfuse 是否可用的标志
_langfuse_enabled = False
_langfuse_instance = None


def _init_langfuse():
    """尝试初始化 Langfuse 客户端"""
    global _langfuse_enabled, _langfuse_instance

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "")
    host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

    if not public_key or not secret_key:
        logger.info("[Langfuse] 未配置 LANGFUSE_PUBLIC_KEY/SECRET_KEY，可观测性追踪已禁用")
        _langfuse_enabled = False
        return

    try:
        from langfuse import Langfuse

        _langfuse_instance = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
        )
        _langfuse_enabled = True
        logger.info("[Langfuse] ✅ 可观测性追踪已启用 | host={}", host)
    except Exception as e:
        logger.warning("[Langfuse] 初始化失败，降级为无追踪模式 | error={}", e)
        _langfuse_enabled = False


def get_langfuse():
    """获取 Langfuse 实例（可能为 None）"""
    global _langfuse_instance
    if _langfuse_instance is None:
        _init_langfuse()
    return _langfuse_instance


def is_enabled() -> bool:
    """Langfuse 是否可用"""
    if _langfuse_instance is None:
        _init_langfuse()
    return _langfuse_enabled


class LLMTracer:
    """
    LLM 调用追踪器

    封装 Langfuse 的 trace/span/generation 概念，提供简洁的 API。
    不可用时自动降级为空操作（不影响业务）。

    用法：
        tracer = LLMTracer(trace_name="rag_query", user_id="user1")
        tracer.log_retrieval(query="年假", results=[...], duration_ms=120)
        tracer.log_generation(
            model="deepseek-chat",
            prompt="...",
            completion="...",
            tokens={"input": 200, "output": 100},
        )
        tracer.end()
    """

    def __init__(
        self,
        trace_name: str,
        user_id: str = "",
        session_id: str = "",
        metadata: dict | None = None,
    ) -> None:
        self._trace = None
        self._enabled = is_enabled()

        if self._enabled:
            langfuse = get_langfuse()
            self._trace = langfuse.trace(
                name=trace_name,
                user_id=user_id or None,
                session_id=session_id or None,
                metadata=metadata or {},
            )

    def log_retrieval(
        self,
        query: str,
        results: list[dict],
        duration_ms: float = 0,
        metadata: dict | None = None,
    ) -> None:
        """记录检索步骤"""
        if not self._enabled or not self._trace:
            return

        self._trace.span(
            name="retrieval",
            input={"query": query},
            output={"results_count": len(results), "results": results[:3]},
            metadata={**(metadata or {}), "duration_ms": duration_ms},
        )

    def log_generation(
        self,
        model: str,
        prompt: str | list,
        completion: str,
        tokens: dict | None = None,
        duration_ms: float = 0,
        metadata: dict | None = None,
    ) -> None:
        """记录 LLM 生成步骤"""
        if not self._enabled or not self._trace:
            return

        usage = None
        if tokens:
            usage = {
                "input": tokens.get("input", 0) or tokens.get("prompt_tokens", 0),
                "output": tokens.get("output", 0) or tokens.get("completion_tokens", 0),
                "total": tokens.get("total", 0) or tokens.get("total_tokens", 0),
            }

        self._trace.generation(
            name="llm_call",
            model=model,
            input=prompt if isinstance(prompt, str) else prompt[:5],
            output=completion[:500],
            usage=usage,
            metadata={**(metadata or {}), "duration_ms": duration_ms},
        )

    def log_tool_call(
        self,
        tool_name: str,
        arguments: dict,
        result: str,
        duration_ms: float = 0,
    ) -> None:
        """记录工具调用"""
        if not self._enabled or not self._trace:
            return

        self._trace.span(
            name=f"tool:{tool_name}",
            input=arguments,
            output={"result_preview": result[:200]},
            metadata={"duration_ms": duration_ms},
        )

    def log_routing(self, user_message: str, routed_to: str) -> None:
        """记录 Multi-Agent 路由决策"""
        if not self._enabled or not self._trace:
            return

        self._trace.span(
            name="routing",
            input={"message": user_message[:100]},
            output={"routed_to": routed_to},
        )

    def set_score(self, name: str, value: float, comment: str = "") -> None:
        """设置评测分数"""
        if not self._enabled or not self._trace:
            return

        self._trace.score(name=name, value=value, comment=comment)

    def end(self) -> None:
        """结束追踪"""
        if self._enabled and _langfuse_instance:
            try:
                _langfuse_instance.flush()
            except Exception:
                pass


def shutdown():
    """关闭 Langfuse 客户端（应用退出时调用）"""
    global _langfuse_instance
    if _langfuse_instance:
        try:
            _langfuse_instance.shutdown()
        except Exception:
            pass
        _langfuse_instance = None
    logger.info("[Langfuse] 客户端已关闭")
