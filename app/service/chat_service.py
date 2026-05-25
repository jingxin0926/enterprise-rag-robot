"""
聊天服务层

核心职责：
1. 管理会话上下文（多轮历史记忆）
2. 组装 prompt（system + history + user input）
3. 调用 LLM，返回流式或普通响应
4. Token 统计

业务编排集中在这一层，对外暴露简洁的方法签名。
"""

import json
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass

from loguru import logger

from app.core.config import settings
from app.infra.cache.redis_client import get_redis
from app.infra.llm.deepseek_client import ChatMessage, ChatResponse, get_deepseek_client

# 系统提示词（后续可以做成可配置 / 多租户）
SYSTEM_PROMPT = """你是一个内部知识助手。你的职责是：
1. 准确回答用户关于公司内部制度、流程、技术文档等方面的问题
2. 如果不确定答案，诚实告知而非编造
3. 回答简洁精准，使用中文
4. 必要时使用 Markdown 格式化输出（列表、代码块等）"""


@dataclass
class TokenUsage:
    """Token 用量统计"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


# 内存会话存储（Redis 不可用时的降级方案）
_memory_store: dict[str, list[dict]] = {}


class ChatService:
    """
    聊天服务

    职责：会话管理 + LLM 调用编排
    """

    def __init__(self) -> None:
        self._llm = get_deepseek_client()

    # ------------------------------------------------------------------
    # 会话历史管理
    # ------------------------------------------------------------------
    async def _get_history(self, session_id: str) -> list[dict]:
        """
        获取会话历史

        优先从 Redis 获取，fallback 到内存
        """
        redis = await get_redis()
        if redis:
            key = f"chat:history:{session_id}"
            raw = await redis.get(key)
            if raw:
                return json.loads(raw)
            return []
        else:
            # 内存 fallback
            return _memory_store.get(session_id, [])

    async def _save_history(self, session_id: str, history: list[dict]) -> None:
        """
        保存会话历史

        限制最大轮数，避免 context window 过长
        """
        # 截断：保留最近 N 轮（一轮 = user + assistant 两条消息）
        max_messages = settings.chat_max_history * 2
        if len(history) > max_messages:
            history = history[-max_messages:]

        redis = await get_redis()
        if redis:
            key = f"chat:history:{session_id}"
            await redis.set(key, json.dumps(history, ensure_ascii=False), ex=settings.chat_session_ttl)
        else:
            _memory_store[session_id] = history

    async def _build_messages(self, session_id: str, user_input: str) -> list[ChatMessage]:
        """
        组装完整的消息列表

        结构：[system_prompt] + [历史消息] + [当前用户输入]
        """
        messages = [ChatMessage(role="system", content=SYSTEM_PROMPT)]

        # 加载历史
        history = await self._get_history(session_id)
        for msg in history:
            messages.append(ChatMessage(role=msg["role"], content=msg["content"]))

        # 当前用户输入
        messages.append(ChatMessage(role="user", content=user_input))
        return messages

    # ------------------------------------------------------------------
    # 对话接口
    # ------------------------------------------------------------------
    async def chat(self, session_id: str, user_input: str) -> ChatResponse:
        """
        普通对话（一次性返回完整答案）

        Args:
            session_id: 会话 ID
            user_input: 用户输入

        Returns:
            ChatResponse 包含答案 + token 用量
        """
        start = time.perf_counter()
        messages = await self._build_messages(session_id, user_input)

        # 调用 LLM
        response = await self._llm.chat(messages)

        # 保存历史（user + assistant）
        history = await self._get_history(session_id)
        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": response.content})
        await self._save_history(session_id, history)

        cost_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "[ChatService] 普通对话 | session={} cost={:.0f}ms tokens={}",
            session_id[:8],
            cost_ms,
            response.total_tokens,
        )
        return response

    async def chat_stream(self, session_id: str, user_input: str) -> AsyncGenerator[str, None]:
        """
        流式对话（逐 token 返回）

        前端通过 SSE 消费，实现打字机效果。
        流式完成后会自动保存会话历史。

        Args:
            session_id: 会话 ID
            user_input: 用户输入

        Yields:
            每次产出一小段文本
        """
        messages = await self._build_messages(session_id, user_input)
        full_content = ""

        # 流式调用 LLM
        async for chunk in self._llm.chat_stream(messages):
            full_content += chunk
            yield chunk

        # 流式结束，保存历史
        history = await self._get_history(session_id)
        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": full_content})
        await self._save_history(session_id, history)

        logger.info(
            "[ChatService] 流式对话完成 | session={} response_len={}",
            session_id[:8],
            len(full_content),
        )

    async def clear_history(self, session_id: str) -> None:
        """清空会话历史"""
        redis = await get_redis()
        if redis:
            await redis.delete(f"chat:history:{session_id}")
        else:
            _memory_store.pop(session_id, None)
        logger.info("[ChatService] 清空会话 | session={}", session_id[:8])
