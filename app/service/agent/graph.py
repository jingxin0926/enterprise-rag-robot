"""
Agent 编排器（基于 OpenAI function calling 协议）

设计思路：
不用 LangGraph 的复杂状态机（对 DeepSeek 兼容性更好），
而是用 OpenAI SDK 原生的 tool_calls 机制实现 Agent 循环：

1. 把工具描述发给 DeepSeek
2. DeepSeek 返回是否需要调用工具
3. 如果需要 → 执行工具 → 把结果反馈 → 再次调用 DeepSeek
4. 如果不需要 → 直接返回回答

这种方式更轻量、更稳定，且 DeepSeek 原生支持 function calling。
"""

import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

from loguru import logger
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageToolCall

from app.core.config import settings
from app.prompts.loader import get_prompt_loader
from app.service.agent.tools import AVAILABLE_TOOLS

# 最大工具调用轮次（防止死循环）
MAX_TOOL_ROUNDS = 3


def _build_tool_schemas() -> list[dict]:
    """
    从 Python 函数自动生成 OpenAI function calling schema

    利用函数的 docstring 和 type hints 生成描述
    """
    tools = []
    for func in AVAILABLE_TOOLS:
        # 解析函数签名
        import inspect
        sig = inspect.signature(func)
        params = {}
        required = []

        for name, param in sig.parameters.items():
            params[name] = {
                "type": "string",
                "description": f"参数: {name}",
            }
            if param.default is inspect.Parameter.empty:
                required.append(name)

        # 从 docstring 提取描述
        doc = func.__doc__ or ""
        # 取第一段作为函数描述
        description = doc.strip().split("\n\n")[0].strip() if doc else func.__name__

        tool_schema = {
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
        }
        tools.append(tool_schema)

    return tools


# 工具名 → 函数映射
_TOOL_MAP = {func.__name__: func for func in AVAILABLE_TOOLS}

# 预构建 schema（启动时一次性生成）
_TOOL_SCHEMAS = _build_tool_schemas()


@dataclass
class AgentResponse:
    """Agent 响应"""

    answer: str
    tool_calls_made: list[dict] = field(default_factory=list)
    total_tokens: int = 0
    rounds: int = 0


def _execute_tool(tool_call: ChatCompletionMessageToolCall) -> str:
    """执行工具调用"""
    func_name = tool_call.function.name
    func = _TOOL_MAP.get(func_name)

    if not func:
        return f"未知工具: {func_name}"

    # 解析参数
    try:
        args = json.loads(tool_call.function.arguments) if tool_call.function.arguments else {}
    except json.JSONDecodeError:
        args = {}

    logger.info("[Agent] 调用工具: {}({})", func_name, args)

    # 执行
    try:
        result = func(**args)
        return result
    except Exception as e:
        logger.error("[Agent] 工具执行失败: {} | {}", func_name, e)
        return f"工具执行失败: {e}"


async def run_agent(
    user_message: str,
    history: list[dict] | None = None,
) -> AgentResponse:
    """
    运行 Agent（非流式）

    Agent 循环：
    用户消息 → LLM(带工具) → [工具调用 → 执行 → 反馈]* → 最终回答

    Args:
        user_message: 用户输入
        history: 历史消息（可选）

    Returns:
        AgentResponse
    """
    client = AsyncOpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        timeout=settings.deepseek_timeout,
    )

    # 构建消息（从 Prompt 文件加载系统提示词）
    agent_system_prompt = get_prompt_loader().load("agent_system")
    messages = [{"role": "system", "content": agent_system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    tool_calls_made = []
    total_tokens = 0
    rounds = 0

    # Agent 循环
    for round_num in range(MAX_TOOL_ROUNDS + 1):
        rounds = round_num + 1

        response = await client.chat.completions.create(
            model=settings.deepseek_model,
            messages=messages,
            tools=_TOOL_SCHEMAS if round_num < MAX_TOOL_ROUNDS else None,  # 最后一轮不提供工具，强制回答
            temperature=settings.deepseek_temperature,
            max_tokens=settings.deepseek_max_tokens,
        )

        if response.usage:
            total_tokens += response.usage.total_tokens

        choice = response.choices[0]
        assistant_message = choice.message

        # 如果没有工具调用，直接返回回答
        if not assistant_message.tool_calls:
            answer = assistant_message.content or ""
            logger.info(
                "[Agent] 完成 | rounds={} tools_called={} tokens={}",
                rounds,
                len(tool_calls_made),
                total_tokens,
            )
            return AgentResponse(
                answer=answer,
                tool_calls_made=tool_calls_made,
                total_tokens=total_tokens,
                rounds=rounds,
            )

        # 有工具调用 → 执行所有工具
        messages.append(assistant_message.model_dump())

        for tool_call in assistant_message.tool_calls:
            result = _execute_tool(tool_call)
            tool_calls_made.append({
                "tool": tool_call.function.name,
                "args": tool_call.function.arguments,
                "result_preview": result[:200],
            })

            # 把工具结果反馈给 LLM
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })

    # 如果达到最大轮次仍未结束
    return AgentResponse(
        answer="抱歉，处理过程过于复杂，请尝试简化问题。",
        tool_calls_made=tool_calls_made,
        total_tokens=total_tokens,
        rounds=rounds,
    )


async def run_agent_stream(
    user_message: str,
    history: list[dict] | None = None,
) -> AsyncGenerator[str, None]:
    """
    运行 Agent（流式）

    工具调用阶段不产出，最终回答阶段流式输出。
    """
    client = AsyncOpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        timeout=settings.deepseek_timeout,
    )

    # 构建消息（从 Prompt 文件加载系统提示词）
    agent_system_prompt = get_prompt_loader().load("agent_system")
    messages = [{"role": "system", "content": agent_system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    # 先做非流式的工具调用循环
    for round_num in range(MAX_TOOL_ROUNDS):
        response = await client.chat.completions.create(
            model=settings.deepseek_model,
            messages=messages,
            tools=_TOOL_SCHEMAS,
            temperature=settings.deepseek_temperature,
            max_tokens=settings.deepseek_max_tokens,
        )

        choice = response.choices[0]
        assistant_message = choice.message

        if not assistant_message.tool_calls:
            # 没有工具调用，直接流式回答
            break

        # 执行工具
        messages.append(assistant_message.model_dump())
        for tool_call in assistant_message.tool_calls:
            result = _execute_tool(tool_call)
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })
    else:
        # 工具循环结束，进入最终回答
        pass

    # 最终回答阶段：流式输出
    stream = await client.chat.completions.create(
        model=settings.deepseek_model,
        messages=messages,
        temperature=settings.deepseek_temperature,
        max_tokens=settings.deepseek_max_tokens,
        stream=True,
    )

    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
