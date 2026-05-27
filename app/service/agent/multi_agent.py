"""
Multi-Agent 多智能体协作编排器

架构设计：
┌─────────────────────────────────────────────────┐
│  Supervisor（路由Agent）                         │
│  - 分析用户意图                                   │
│  - 决定转发给哪个专家 Agent                        │
│  - 整合多轮结果                                   │
└─────────────────────────────────────────────────┘
         ↓              ↓              ↓
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ KnowledgeAgent│ │  ChatAgent   │ │  DataAgent   │
│ 知识库问答专家 │ │ 通用对话专家  │ │ 数据计算专家  │
│ (带RAG工具)   │ │ (纯对话)     │ │ (计算+时间)  │
└──────────────┘ └──────────────┘ └──────────────┘

工作流程：
1. 用户消息进入 Supervisor
2. Supervisor 判断意图，输出目标 Agent 名称
3. 路由到对应 Agent 执行（各 Agent 有独立的工具和 Prompt）
4. 返回结果 + 路由信息

优势（面试讲点）：
- 职责分离：每个 Agent 只关注自己的领域，Prompt 更精准
- 可扩展：新增领域只需加一个 Agent + Prompt，不影响已有逻辑
- 可观测：返回路由路径，方便排查"为什么回答不对"
"""

import json
from dataclasses import dataclass, field

from loguru import logger
from openai import AsyncOpenAI

from app.core.config import settings
from app.infra.llm.deepseek_client import ChatMessage, get_deepseek_client
from app.prompts.loader import get_prompt_loader
from app.service.agent.tools import AVAILABLE_TOOLS, calculator, get_current_time, search_knowledge_base


@dataclass
class MultiAgentResponse:
    """Multi-Agent 响应"""

    answer: str                                              # 最终回答
    routed_to: str                                           # 被路由到哪个 Agent
    routing_reason: str = ""                                 # 路由原因（可选）
    tool_calls_made: list[dict] = field(default_factory=list)  # 工具调用记录
    total_tokens: int = 0                                    # 总 token 消耗
    rounds: int = 0                                          # Agent 内部循环轮次


# Agent 名称到工具映射
_AGENT_TOOLS = {
    "knowledge_agent": [search_knowledge_base],
    "chat_agent": [],       # 纯对话，不需要工具
    "data_agent": [get_current_time, calculator],
}

# 有效的 Agent 名称
_VALID_AGENTS = {"knowledge_agent", "chat_agent", "data_agent"}


def _build_tool_schemas(tools: list) -> list[dict]:
    """为指定工具列表构建 OpenAI function calling schema"""
    import inspect

    schemas = []
    for func in tools:
        sig = inspect.signature(func)
        params = {}
        required = []

        for name, param in sig.parameters.items():
            params[name] = {"type": "string", "description": f"参数: {name}"}
            if param.default is inspect.Parameter.empty:
                required.append(name)

        doc = func.__doc__ or ""
        description = doc.strip().split("\n\n")[0].strip() if doc else func.__name__

        schemas.append({
            "type": "function",
            "function": {
                "name": func.__name__,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": params,
                    "required": required,
                } if params else {"type": "object", "properties": {}},
            },
        })
    return schemas


def _execute_tool(func_name: str, arguments: str) -> str:
    """执行工具调用"""
    tool_map = {func.__name__: func for func in AVAILABLE_TOOLS}
    func = tool_map.get(func_name)

    if not func:
        return f"未知工具: {func_name}"

    try:
        args = json.loads(arguments) if arguments else {}
        result = func(**args)
        logger.info("[MultiAgent] 工具调用: {}({}) → {}...", func_name, args, str(result)[:50])
        return result
    except Exception as e:
        logger.error("[MultiAgent] 工具执行失败: {} | {}", func_name, e)
        return f"工具执行失败: {e}"


async def _route(user_message: str, history: list[dict] | None = None) -> str:
    """
    Supervisor 路由：分析用户意图，决定交给哪个 Agent

    Args:
        user_message: 用户消息
        history: 历史对话（辅助判断意图）

    Returns:
        agent 名称（knowledge_agent / chat_agent / data_agent）
    """
    llm = get_deepseek_client()
    loader = get_prompt_loader()

    supervisor_prompt = loader.load("supervisor")
    messages = [
        ChatMessage(role="system", content=supervisor_prompt),
    ]

    # 如果有历史，加入最近几条辅助判断
    if history:
        for msg in history[-4:]:
            messages.append(ChatMessage(role=msg["role"], content=msg["content"]))

    messages.append(ChatMessage(role="user", content=user_message))

    response = await llm.chat(messages, max_tokens=50, temperature=0.1)
    agent_name = response.content.strip().lower()

    # 校验返回值
    if agent_name not in _VALID_AGENTS:
        logger.warning("[Supervisor] 路由结果无效: '{}', 降级到 chat_agent", agent_name)
        agent_name = "chat_agent"

    logger.info("[Supervisor] 路由决策: '{}...' → {}", user_message[:20], agent_name)
    return agent_name


async def _run_specialist(
    agent_name: str,
    user_message: str,
    history: list[dict] | None = None,
) -> MultiAgentResponse:
    """
    运行专家 Agent

    根据 agent_name 加载对应的 Prompt 和工具，执行任务。
    """
    loader = get_prompt_loader()
    client = AsyncOpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        timeout=settings.deepseek_timeout,
    )

    # 加载专家 Prompt
    prompt_name = agent_name.replace("_agent", "_agent")  # knowledge_agent → knowledge_agent
    system_prompt = loader.load(prompt_name)

    # 构建消息
    messages = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history[-6:])
    messages.append({"role": "user", "content": user_message})

    # 获取该 Agent 的工具
    tools = _AGENT_TOOLS.get(agent_name, [])
    tool_schemas = _build_tool_schemas(tools) if tools else None

    tool_calls_made = []
    total_tokens = 0
    max_rounds = 3

    # Agent 循环（有工具的才需要循环）
    for round_num in range(max_rounds + 1):
        response = await client.chat.completions.create(
            model=settings.deepseek_model,
            messages=messages,
            tools=tool_schemas if round_num < max_rounds else None,
            temperature=settings.deepseek_temperature,
            max_tokens=settings.deepseek_max_tokens,
        )

        if response.usage:
            total_tokens += response.usage.total_tokens

        choice = response.choices[0]
        assistant_message = choice.message

        # 没有工具调用 → 直接返回
        if not assistant_message.tool_calls:
            return MultiAgentResponse(
                answer=assistant_message.content or "",
                routed_to=agent_name,
                tool_calls_made=tool_calls_made,
                total_tokens=total_tokens,
                rounds=round_num + 1,
            )

        # 有工具调用 → 执行
        messages.append(assistant_message.model_dump())
        for tool_call in assistant_message.tool_calls:
            result = _execute_tool(tool_call.function.name, tool_call.function.arguments)
            tool_calls_made.append({
                "tool": tool_call.function.name,
                "args": tool_call.function.arguments,
                "result_preview": result[:200],
            })
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })

    # 超过最大轮次
    return MultiAgentResponse(
        answer="处理过程较复杂，请尝试简化问题。",
        routed_to=agent_name,
        tool_calls_made=tool_calls_made,
        total_tokens=total_tokens,
        rounds=max_rounds,
    )


async def run_multi_agent(
    user_message: str,
    history: list[dict] | None = None,
) -> MultiAgentResponse:
    """
    Multi-Agent 入口

    流程：Supervisor 路由 → 专家 Agent 执行 → 返回结果

    Args:
        user_message: 用户消息
        history: 历史对话上下文

    Returns:
        MultiAgentResponse 包含回答 + 路由信息 + 工具调用记录
    """
    # Step 1: Supervisor 路由
    agent_name = await _route(user_message, history)

    # Step 2: 专家 Agent 执行
    result = await _run_specialist(agent_name, user_message, history)

    logger.info(
        "[MultiAgent] 完成 | routed_to={} rounds={} tokens={} tools={}",
        result.routed_to,
        result.rounds,
        result.total_tokens,
        len(result.tool_calls_made),
    )

    return result
