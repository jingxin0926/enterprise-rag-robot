"""
Agent 可用工具定义

每个工具就是一个函数，Agent 根据用户意图自主决定是否调用。
工具集合类似插件机制，可按需扩展。
"""

from datetime import datetime

from loguru import logger

from app.infra.vector.qdrant_store import get_qdrant_store


def search_knowledge_base(query: str) -> str:
    """
    搜索企业内部知识库。当用户询问公司制度、流程、规范、文档相关问题时使用此工具。

    Args:
        query: 搜索查询语句

    Returns:
        检索到的相关文档内容，如果没找到会返回提示信息
    """
    try:
        store = get_qdrant_store()
        results = store.search(query=query, top_k=3, score_threshold=0.3)

        if not results:
            return "知识库中未找到相关内容。"

        # 组装结果
        parts = []
        for i, r in enumerate(results, 1):
            source = r.metadata.get("source", "未知")
            parts.append(f"[{i}] (来源: {source})\n{r.content}")

        logger.info("[Tool:search_kb] query='{}' hits={}", query[:30], len(results))
        return "\n\n".join(parts)
    except Exception as e:
        logger.error("[Tool:search_kb] 错误: {}", e)
        return f"知识库检索出错: {e}"


def get_current_time() -> str:
    """
    获取当前日期和时间。当用户询问"今天几号"、"现在几点"等时间相关问题时使用。

    Returns:
        当前日期时间字符串
    """
    now = datetime.now()
    return now.strftime("当前时间：%Y年%m月%d日 %H:%M:%S（星期%w）").replace(
        "星期0", "星期日"
    ).replace("星期1", "星期一").replace("星期2", "星期二").replace(
        "星期3", "星期三"
    ).replace("星期4", "星期四").replace("星期5", "星期五").replace("星期6", "星期六")


def calculator(expression: str) -> str:
    """
    数学计算器。当用户需要进行数学计算时使用（加减乘除、百分比等）。

    Args:
        expression: 数学表达式，例如 "100 * 1.5" 或 "(36 + 24) / 2"

    Returns:
        计算结果
    """
    try:
        # 安全的数学表达式计算（只允许数字和基础运算符）
        allowed_chars = set("0123456789+-*/().% ")
        if not all(c in allowed_chars for c in expression):
            return "不支持的表达式。仅支持数字和基础运算符（+ - * / % ()）"

        result = eval(expression)  # noqa: S307 - 已做输入过滤
        logger.info("[Tool:calculator] {} = {}", expression, result)
        return f"{expression} = {result}"
    except Exception as e:
        return f"计算错误: {e}"


# 工具注册表（供 Agent 使用）
AVAILABLE_TOOLS = [search_knowledge_base, get_current_time, calculator]
