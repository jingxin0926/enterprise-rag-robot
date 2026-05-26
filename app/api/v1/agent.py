"""
Agent 智能对话接口

与普通 Chat 的区别：
- Chat: 纯对话，不会主动检索
- Agent: 自主决策，自动判断是否需要查知识库/用工具

这是最终面向用户的主接口，用户不需要关心"该用闲聊还是知识问答"。
"""

import uuid

from fastapi import APIRouter, Depends, Request
from loguru import logger
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.api.deps import get_current_user, get_optional_user
from app.core.response import R
from app.core.security import TokenPayload
from app.middleware.rate_limit import limiter
from app.middleware.trace import get_trace_id
from app.service.agent.graph import run_agent, run_agent_stream
from app.service.memory import ConversationMemory
from app.service.token_tracker import get_token_tracker

router = APIRouter(prefix="/agent", tags=["智能助手"])

# 记忆管理器（全局复用）
_memory = ConversationMemory()


class AgentRequest(BaseModel):
    """Agent 请求"""

    message: str = Field(..., min_length=1, max_length=4000, description="用户消息")
    session_id: str | None = Field(default=None, description="会话 ID")
    stream: bool = Field(default=True, description="是否流式输出")


@router.post("/chat", summary="智能对话（Agent）")
@limiter.limit("20/minute")  # Agent 接口限制更严格（消耗 LLM token）
async def agent_chat(request: Request, req: AgentRequest, user: TokenPayload | None = Depends(get_optional_user)):
    """
    Agent 智能对话

    Agent 会自动判断：
    - 需要查知识库 → 调用 search_knowledge_base 工具
    - 需要计算 → 调用 calculator 工具
    - 需要时间 → 调用 get_current_time 工具
    - 普通闲聊 → 直接回答
    """
    session_id = req.session_id or uuid.uuid4().hex

    # 获取历史上下文（含长期记忆摘要 + 近期对话）
    history = await _memory.get_context(session_id)

    # 流式输出
    if req.stream:
        async def event_generator():
            try:
                full_answer = ""
                async for chunk in run_agent_stream(req.message, history=history):
                    full_answer += chunk
                    yield {"event": "message", "data": chunk}

                # 流式结束后保存记忆
                await _memory.add_message(session_id, "user", req.message)
                await _memory.add_message(session_id, "assistant", full_answer)

                yield {"event": "done", "data": f'{{"session_id": "{session_id}"}}'}
            except Exception as e:
                logger.exception("[Agent SSE] 异常: {}", e)
                yield {"event": "error", "data": str(e)}

        return EventSourceResponse(event_generator())

    # 非流式输出
    result = await run_agent(req.message, history=history)

    # 保存记忆
    await _memory.add_message(session_id, "user", req.message)
    await _memory.add_message(session_id, "assistant", result.answer)

    # Token 追踪（如果已认证）
    if user and result.total_tokens > 0:
        tracker = get_token_tracker()
        tracker.record(
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            model="deepseek-chat",
            prompt_tokens=int(result.total_tokens * 0.7),
            completion_tokens=int(result.total_tokens * 0.3),
            endpoint="agent",
        )

    # 获取记忆状态
    memory_stats = await _memory.get_stats(session_id)

    return R.success(
        data={
            "answer": result.answer,
            "session_id": session_id,
            "tool_calls": result.tool_calls_made,
            "total_tokens": result.total_tokens,
            "rounds": result.rounds,
            "memory": memory_stats,
        },
        trace_id=get_trace_id(),
    )
