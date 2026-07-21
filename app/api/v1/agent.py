"""
Agent 智能对话接口

统一入口设计：
- POST /api/v1/agent/chat — 统一智能对话入口（自动判断复杂度，选择最佳策略）
- POST /api/v1/agent/multi — Multi-Agent 协作（内部/调试用）
- POST /api/v1/agent/plan — Plan-and-Execute（内部/调试用）

用户只需调 /chat 一个接口，系统自动判断走哪种模式。
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from loguru import logger
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.api.deps import get_current_user
from app.core.response import R
from app.core.security import TokenPayload
from app.infra.llm.deepseek_client import ChatMessage, get_deepseek_client
from app.middleware.rate_limit import limiter
from app.middleware.trace import get_trace_id
from app.prompts.loader import get_prompt_loader
from app.service.agent.graph import run_agent, run_agent_stream
from app.service.agent.knowledge_ops import KnowledgeOpsAgent
from app.service.agent.multi_agent import run_multi_agent
from app.service.memory import ConversationMemory
from app.service.token_tracker import get_token_tracker

router = APIRouter(prefix="/agent", tags=["智能助手"])

# 记忆管理器（全局复用）
_memory = ConversationMemory()


async def _judge_complexity(message: str) -> str:
    """
    判断用户消息的复杂度，决定走哪种处理模式

    Returns:
        "simple" / "moderate" / "complex"
    """
    llm = get_deepseek_client()
    loader = get_prompt_loader()

    prompt = loader.load("complexity_judge")
    response = await llm.chat(
        [ChatMessage(role="system", content=prompt), ChatMessage(role="user", content=message)],
        max_tokens=20,
        temperature=0.1,
    )
    result = response.content.strip().lower()

    if result not in ("simple", "moderate", "complex"):
        result = "moderate"  # 默认走 Multi-Agent

    return result


class AgentRequest(BaseModel):
    """Agent 请求"""

    message: str = Field(..., min_length=1, max_length=4000, description="用户消息")
    session_id: str | None = Field(default=None, description="会话 ID")
    stream: bool = Field(default=False, description="是否流式输出")


class KnowledgeOpsRequest(BaseModel):
    """知识运营 Agent 请求。"""

    message: str = Field(..., min_length=1, max_length=2_000, description="管理员的知识运营问题")


@router.post("/knowledge-ops", summary="知识运营 Agent（只读、可审计）")
@limiter.limit("15/minute")
async def knowledge_ops_agent(
    request: Request,
    req: KnowledgeOpsRequest,
    user: TokenPayload = Depends(get_current_user),
):
    """在当前租户内执行只读、可审计的知识库运营查询。"""
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅管理员可使用知识运营 Agent")

    result = await KnowledgeOpsAgent(
        tenant_id=user.tenant_id,
        operator_id=user.user_id,
        trace_id=get_trace_id(),
    ).run(req.message)
    return R.success(
        data={
            "answer": result.answer,
            "tool_calls": result.tool_calls,
            "total_tokens": result.total_tokens,
            "rounds": result.rounds,
        },
        trace_id=get_trace_id(),
    )


@router.post("/chat", summary="智能对话（统一入口）")
@limiter.limit("20/minute")
async def agent_chat(request: Request, req: AgentRequest, user: TokenPayload = Depends(get_current_user)):
    """
    智能对话统一入口

    系统自动判断请求复杂度，选择最佳处理策略：
    - simple：单 Agent 直接回答（省 token）
    - moderate：Multi-Agent 路由到专家处理
    - complex：Task Planning 拆解多步执行

    需要认证（Bearer Token），用于租户隔离和计费。
    """
    from app.service.agent.task_planner import run_with_planning

    session_id = req.session_id or uuid.uuid4().hex

    # 获取历史上下文（含长期记忆摘要 + 近期对话）
    history = await _memory.get_context(session_id)

    # 流式输出（走单 Agent，流式不支持 Planning）
    if req.stream:
        async def event_generator():
            try:
                full_answer = ""
                async for chunk in run_agent_stream(req.message, history=history):
                    full_answer += chunk
                    yield {"event": "message", "data": chunk}

                await _memory.add_message(session_id, "user", req.message)
                await _memory.add_message(session_id, "assistant", full_answer)

                yield {"event": "done", "data": f'{{"session_id": "{session_id}"}}'}
            except Exception as e:
                logger.exception("[Agent SSE] 异常: {}", e)
                yield {"event": "error", "data": str(e)}

        return EventSourceResponse(event_generator())

    # 非流式：自动判断复杂度
    complexity = await _judge_complexity(req.message)
    logger.info("[Agent] 复杂度判断: '{}...' → {}", req.message[:20], complexity)

    # 根据复杂度选择策略
    mode = complexity
    if complexity == "simple":
        result = await run_agent(req.message, history=history)
        answer = result.answer
        extra = {"tool_calls": result.tool_calls_made, "rounds": result.rounds}
    elif complexity == "complex":
        plan_result = await run_with_planning(req.message, history=history)
        answer = plan_result.answer
        extra = {
            "tasks": [{"step": t.step, "description": t.description, "agent": t.agent} for t in plan_result.tasks],
            "tool_calls": plan_result.tool_calls_made,
        }
    else:
        multi_result = await run_multi_agent(req.message, history=history)
        answer = multi_result.answer
        extra = {"routed_to": multi_result.routed_to, "tool_calls": multi_result.tool_calls_made}

    # 保存记忆
    await _memory.add_message(session_id, "user", req.message)
    await _memory.add_message(session_id, "assistant", answer)

    # Token 追踪
    total_tokens = extra.get("total_tokens", 0) or 0
    if user and total_tokens > 0:
        tracker = get_token_tracker()
        tracker.record(
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            model="deepseek-chat",
            prompt_tokens=int(total_tokens * 0.7),
            completion_tokens=int(total_tokens * 0.3),
            endpoint="agent",
        )

    # 记忆状态
    memory_stats = await _memory.get_stats(session_id)

    return R.success(
        data={
            "answer": answer,
            "session_id": session_id,
            "mode": mode,
            "memory": memory_stats,
            **extra,
        },
        trace_id=get_trace_id(),
    )


class MultiAgentRequest(BaseModel):
    """Multi-Agent 请求"""

    message: str = Field(..., min_length=1, max_length=4000, description="用户消息")
    session_id: str | None = Field(default=None, description="会话 ID")


@router.post("/multi", summary="Multi-Agent 智能对话")
@limiter.limit("20/minute")
async def multi_agent_chat(request: Request, req: MultiAgentRequest, user: TokenPayload = Depends(get_current_user)):
    """
    Multi-Agent 协作对话

    与单 Agent 的区别：
    - 单 Agent：一个 Agent 判断所有事情
    - Multi-Agent：Supervisor 先分析意图，路由到专家 Agent 处理

    架构：Supervisor（路由）→ KnowledgeAgent / ChatAgent / DataAgent

    返回值中包含 routed_to 字段，标识本次由哪个专家处理。
    """
    session_id = req.session_id or uuid.uuid4().hex

    # 获取历史上下文
    history = await _memory.get_context(session_id)

    # Multi-Agent 执行
    result = await run_multi_agent(req.message, history=history)

    # 保存记忆
    await _memory.add_message(session_id, "user", req.message)
    await _memory.add_message(session_id, "assistant", result.answer)

    # Token 追踪
    if result.total_tokens > 0:
        tracker = get_token_tracker()
        tracker.record(
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            model="deepseek-chat",
            prompt_tokens=int(result.total_tokens * 0.7),
            completion_tokens=int(result.total_tokens * 0.3),
            endpoint="multi_agent",
        )

    # 记忆状态
    memory_stats = await _memory.get_stats(session_id)

    return R.success(
        data={
            "answer": result.answer,
            "session_id": session_id,
            "routed_to": result.routed_to,
            "tool_calls": result.tool_calls_made,
            "total_tokens": result.total_tokens,
            "rounds": result.rounds,
            "memory": memory_stats,
        },
        trace_id=get_trace_id(),
    )


class PlanAgentRequest(BaseModel):
    """Plan-and-Execute 请求"""

    message: str = Field(..., min_length=1, max_length=4000, description="用户消息")
    session_id: str | None = Field(default=None, description="会话 ID")


@router.post("/plan", summary="Plan-and-Execute 智能对话")
@limiter.limit("15/minute")
async def plan_agent_chat(request: Request, req: PlanAgentRequest, user: TokenPayload = Depends(get_current_user)):
    """
    Plan-and-Execute 模式对话

    与普通 Agent 的区别：
    - 普通 Agent：边想边做
    - Plan-and-Execute：先拆解任务 → 按计划逐步执行 → 综合结果

    适合复杂问题（如"查制度再算数据"、"对比多个信息源"）
    """
    from app.service.agent.task_planner import run_with_planning

    session_id = req.session_id or uuid.uuid4().hex

    # 获取历史上下文
    history = await _memory.get_context(session_id)

    # Plan-and-Execute 执行
    result = await run_with_planning(req.message, history=history)

    # 保存记忆
    await _memory.add_message(session_id, "user", req.message)
    await _memory.add_message(session_id, "assistant", result.answer)

    # Token 追踪
    if result.total_tokens > 0:
        tracker = get_token_tracker()
        tracker.record(
            tenant_id=user.tenant_id,
            user_id=user.user_id,
            model="deepseek-chat",
            prompt_tokens=int(result.total_tokens * 0.7),
            completion_tokens=int(result.total_tokens * 0.3),
            endpoint="plan_agent",
        )

    # 记忆状态
    memory_stats = await _memory.get_stats(session_id)

    return R.success(
        data={
            "answer": result.answer,
            "session_id": session_id,
            "need_planning": result.need_planning,
            "tasks": [
                {
                    "step": t.step,
                    "description": t.description,
                    "agent": t.agent,
                    "result_preview": t.result[:200] if t.result else "",
                }
                for t in result.tasks
            ],
            "tool_calls": result.tool_calls_made,
            "total_tokens": result.total_tokens,
            "memory": memory_stats,
        },
        trace_id=get_trace_id(),
    )
