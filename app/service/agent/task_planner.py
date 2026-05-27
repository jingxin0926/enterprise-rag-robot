"""
Task Planning（任务拆解规划）

核心思想：
- 简单问题：直接回答（不拆解）
- 复杂问题：先拆解为子任务 → 按顺序执行 → 综合结果

这是 Plan-and-Execute 模式，比单纯的 ReAct 循环更高级：
- ReAct：边想边做（适合简单任务）
- Plan-and-Execute：先规划再执行（适合复杂多步任务）

典型场景：
- "帮我查一下年假制度，然后算算我还剩几天"
  → 拆为：1. 查年假制度（knowledge_agent）2. 计算剩余天数（data_agent）
- "对比一下我们和竞品的报销标准，整理成表格"
  → 拆为：1. 查我司报销标准 2. 查竞品标准 3. 整理对比表格
"""

import json
from dataclasses import dataclass, field

from loguru import logger

from app.infra.llm.deepseek_client import ChatMessage, get_deepseek_client
from app.prompts.loader import get_prompt_loader
from app.service.agent.multi_agent import MultiAgentResponse, _run_specialist


@dataclass
class TaskStep:
    """单个子任务"""

    step: int                    # 步骤序号
    description: str             # 任务描述
    agent: str                   # 负责的 Agent（knowledge_agent/chat_agent/data_agent）
    depends_on: list[int] = field(default_factory=list)  # 依赖哪些前置步骤
    result: str = ""             # 执行结果


@dataclass
class PlanResult:
    """任务规划 + 执行的完整结果"""

    answer: str                                              # 最终综合回答
    need_planning: bool = False                              # 是否触发了规划
    tasks: list[TaskStep] = field(default_factory=list)      # 子任务列表
    total_tokens: int = 0                                    # 总 token 消耗
    tool_calls_made: list[dict] = field(default_factory=list)  # 工具调用记录


async def plan_tasks(user_message: str, history: list[dict] | None = None) -> list[TaskStep]:
    """
    任务规划：分析用户请求，决定是否需要拆解为子任务

    Args:
        user_message: 用户消息
        history: 历史对话

    Returns:
        子任务列表（如果不需要拆解，返回单任务）
    """
    llm = get_deepseek_client()
    loader = get_prompt_loader()

    planner_prompt = loader.load("task_planner")
    messages = [
        ChatMessage(role="system", content=planner_prompt),
        ChatMessage(role="user", content=user_message),
    ]

    response = await llm.chat(messages, max_tokens=500, temperature=0.1)

    # 解析 JSON 结果
    raw = response.content.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    try:
        plan = json.loads(raw)
        tasks = []
        for t in plan.get("tasks", []):
            tasks.append(TaskStep(
                step=t.get("step", 1),
                description=t.get("description", ""),
                agent=t.get("agent", "chat_agent"),
                depends_on=t.get("depends_on", []),
            ))

        logger.info(
            "[TaskPlanner] 规划完成 | need_planning={} tasks={}",
            plan.get("need_planning", False),
            len(tasks),
        )
        return tasks

    except json.JSONDecodeError as e:
        logger.warning("[TaskPlanner] JSON解析失败，降级为单任务 | error={}", e)
        return [TaskStep(step=1, description=user_message, agent="chat_agent")]


async def run_with_planning(
    user_message: str,
    history: list[dict] | None = None,
) -> PlanResult:
    """
    Plan-and-Execute 入口

    流程：
    1. Task Planner 分析是否需要拆解
    2. 如果不需要 → 直接交给对应 Agent 执行（退化为普通 Multi-Agent）
    3. 如果需要 → 按顺序执行子任务 → 最后综合所有结果生成最终回答

    Args:
        user_message: 用户消息
        history: 历史对话

    Returns:
        PlanResult 包含最终回答 + 规划详情
    """
    # Step 1: 规划
    tasks = await plan_tasks(user_message, history)

    result = PlanResult(answer="", need_planning=len(tasks) > 1, tasks=tasks)
    total_tokens = 0
    all_tool_calls = []

    # Step 2: 按顺序执行子任务
    step_results = {}

    for task in tasks:
        # 构建子任务的上下文（包含前置步骤的结果）
        task_context = task.description
        if task.depends_on:
            deps_info = "\n".join(
                [f"[步骤{d}的结果]: {step_results.get(d, '无')}" for d in task.depends_on]
            )
            task_context = f"{task.description}\n\n参考前序步骤结果：\n{deps_info}"

        # 调用对应的专家 Agent
        agent_result: MultiAgentResponse = await _run_specialist(
            agent_name=task.agent,
            user_message=task_context,
            history=history,
        )

        # 保存结果
        task.result = agent_result.answer
        step_results[task.step] = agent_result.answer
        total_tokens += agent_result.total_tokens
        all_tool_calls.extend(agent_result.tool_calls_made)

        logger.info(
            "[TaskPlanner] 步骤{}完成 | agent={} result_len={}",
            task.step,
            task.agent,
            len(agent_result.answer),
        )

    # Step 3: 综合所有结果
    if len(tasks) == 1:
        # 单任务，直接返回结果
        result.answer = tasks[0].result
    else:
        # 多任务，用 LLM 综合
        result.answer = await _synthesize(user_message, tasks)
        total_tokens += 200  # 估算综合步骤消耗

    result.total_tokens = total_tokens
    result.tool_calls_made = all_tool_calls

    logger.info(
        "[TaskPlanner] 全部完成 | tasks={} tokens={} planning={}",
        len(tasks),
        total_tokens,
        result.need_planning,
    )
    return result


async def _synthesize(user_message: str, tasks: list[TaskStep]) -> str:
    """
    综合多个子任务的结果，生成最终回答

    Args:
        user_message: 用户原始问题
        tasks: 已执行完的子任务列表（含结果）

    Returns:
        综合后的最终回答
    """
    llm = get_deepseek_client()

    steps_text = "\n\n".join(
        [f"【步骤{t.step}: {t.description}】\n{t.result}" for t in tasks]
    )

    prompt = f"""用户的原始问题是：{user_message}

以下是分步执行的结果：

{steps_text}

请综合以上所有步骤的结果，给用户一个完整、连贯的最终回答。使用中文，支持 Markdown 格式。"""

    response = await llm.chat(
        [ChatMessage(role="user", content=prompt)],
        max_tokens=1000,
        temperature=0.3,
    )

    return response.content
