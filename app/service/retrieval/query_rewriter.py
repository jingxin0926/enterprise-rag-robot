"""
Query 改写（Query Rewriting）

用途：
1. 用户原始问题可能模糊、口语化、省略主语
2. 用 LLM 改写为更适合检索的规范表述
3. 多路召回：原始 query + 改写 query 双路检索，提升召回率

花费：约 100 token/次 ≈ ¥0.00005，基本可忽略
"""

from loguru import logger

from app.infra.llm.deepseek_client import ChatMessage, get_deepseek_client
from app.prompts.loader import get_prompt_loader


class QueryRewriter:
    """
    查询改写器

    用法：
        rewriter = QueryRewriter()
        rewritten = await rewriter.rewrite("休假咋整")
        # → "公司休假制度和请假流程"
    """

    def __init__(self) -> None:
        self._llm = get_deepseek_client()

    async def rewrite(self, question: str) -> str:
        """
        改写查询

        如果改写失败（超时等），返回原始查询（降级处理）

        Args:
            question: 原始用户问题

        Returns:
            改写后的查询文本
        """
        try:
            # 从 Prompt 文件加载改写指令
            rewrite_prompt = get_prompt_loader().load("query_rewriter")
            messages = [
                ChatMessage(role="system", content=rewrite_prompt),
                ChatMessage(role="user", content=question),
            ]
            response = await self._llm.chat(
                messages,
                max_tokens=100,  # 改写很短，限制输出
                temperature=0.1,  # 低温度，确保稳定
            )
            rewritten = response.content.strip()

            # 如果改写结果太短或异常，用原始 query
            if len(rewritten) < 2:
                return question

            logger.info(
                "[QueryRewrite] '{}' → '{}'",
                question[:30],
                rewritten[:50],
            )
            return rewritten

        except Exception as e:
            logger.warning("[QueryRewrite] 改写失败，使用原始查询 | error={}", e)
            return question
