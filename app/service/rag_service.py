"""
RAG 检索增强生成服务（P3 升级版）

检索链路：
1. Query 改写（可选）→ 多路 query
2. 混合检索：BM25 + Vector → RRF 融合 → Rerank 精排
3. 上下文组装 + LLM 生成（带引用来源）

对比 P2：
- P2: 纯向量检索 → LLM
- P3: Query 改写 + BM25 + Vector + RRF + Rerank → LLM（质量大幅提升）
"""

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

from loguru import logger

from app.infra.llm.deepseek_client import ChatMessage, get_deepseek_client
from app.infra.vector.qdrant_store import get_qdrant_store
from app.service.retrieval.hybrid_retriever import HybridResult, get_hybrid_retriever
from app.service.retrieval.query_rewriter import QueryRewriter

# RAG Prompt 模板
RAG_SYSTEM_PROMPT = """你是企业内部知识助手。请严格根据下方【参考资料】回答用户问题。

规则：
1. 只使用参考资料中的信息回答，不要编造内容
2. 如果参考资料不足以回答问题，诚实告知"根据现有资料无法回答该问题"
3. 回答时在末尾注明引用来源，格式：[来源: 文件名]
4. 使用中文回答，简洁准确
5. 支持 Markdown 格式化输出"""

RAG_USER_TEMPLATE = """【参考资料】
{context}

【用户问题】
{question}"""


@dataclass
class RAGResponse:
    """RAG 响应"""

    answer: str
    sources: list[dict] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    # P3 新增：检索相关信息
    rewritten_query: str = ""
    retrieval_mode: str = "hybrid"  # "vector" / "hybrid"


class RAGService:
    """
    RAG 检索增强生成服务（P3 版）

    支持两种模式：
    - mode="vector": 纯向量检索（P2 兼容）
    - mode="hybrid": 混合检索 + Rerank（P3 默认）
    """

    def __init__(
        self,
        top_k: int = 5,
        score_threshold: float = 0.3,
        use_hybrid: bool = True,
        use_rewrite: bool = True,
        use_rerank: bool = True,
    ) -> None:
        self._top_k = top_k
        self._score_threshold = score_threshold
        self._use_hybrid = use_hybrid
        self._use_rewrite = use_rewrite
        self._llm = get_deepseek_client()
        self._rewriter = QueryRewriter() if use_rewrite else None
        self._hybrid = get_hybrid_retriever() if use_hybrid else None
        self._vector_store = get_qdrant_store()

    def _build_context(self, results: list[HybridResult]) -> tuple[str, list[dict]]:
        """将检索结果组装为上下文文本"""
        if not results:
            return "", []

        context_parts = []
        sources = []
        seen = set()  # 去重

        for i, r in enumerate(results, 1):
            # 去重（同一段内容不重复引用）
            if r.content in seen:
                continue
            seen.add(r.content)

            source_name = r.metadata.get("source", "未知")
            context_parts.append(f"[{i}] (来源: {source_name})\n{r.content}")
            sources.append({
                "source": source_name,
                "score": round(r.score, 4),
                "chunk_index": r.metadata.get("chunk_index", 0),
                "retrieval_type": r.source_type,
            })

        return "\n\n".join(context_parts), sources

    async def _retrieve(self, question: str) -> tuple[list[HybridResult], str]:
        """
        执行检索流程

        Returns:
            (results, rewritten_query)
        """
        # Query 改写
        rewritten = question
        if self._rewriter:
            rewritten = await self._rewriter.rewrite(question)

        # 混合检索
        if self._hybrid:
            results = self._hybrid.search(
                query=rewritten,
                top_k=self._top_k,
                score_threshold=self._score_threshold,
            )
        else:
            # Fallback 到纯向量
            vector_results = self._vector_store.search(
                query=rewritten,
                top_k=self._top_k,
                score_threshold=self._score_threshold,
            )
            results = [
                HybridResult(
                    content=r.content,
                    score=r.score,
                    metadata=r.metadata,
                    source_type="vector",
                )
                for r in vector_results
            ]

        return results, rewritten

    async def query(self, question: str) -> RAGResponse:
        """RAG 问答（非流式）"""
        # 1. 检索
        results, rewritten = await self._retrieve(question)

        # 2. 组装上下文
        context, sources = self._build_context(results)

        if not context:
            return RAGResponse(
                answer="抱歉，知识库中暂未找到与您问题相关的内容。请尝试换个说法，或确认相关文档是否已上传。",
                sources=[],
                rewritten_query=rewritten,
            )

        # 3. 构建消息
        user_content = RAG_USER_TEMPLATE.format(context=context, question=question)
        messages = [
            ChatMessage(role="system", content=RAG_SYSTEM_PROMPT),
            ChatMessage(role="user", content=user_content),
        ]

        # 4. 调用 LLM
        response = await self._llm.chat(messages)

        logger.info(
            "[RAG-P3] 问答完成 | question='{}...' rewritten='{}...' hits={} tokens={}",
            question[:20],
            rewritten[:20],
            len(results),
            response.total_tokens,
        )

        return RAGResponse(
            answer=response.content,
            sources=sources,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            total_tokens=response.total_tokens,
            rewritten_query=rewritten,
            retrieval_mode="hybrid" if self._use_hybrid else "vector",
        )

    async def query_stream(self, question: str) -> AsyncGenerator[str, None]:
        """RAG 问答（流式）"""
        # 1. 检索
        results, rewritten = await self._retrieve(question)

        # 2. 组装上下文
        context, sources = self._build_context(results)

        if not context:
            yield "抱歉，知识库中暂未找到与您问题相关的内容。请尝试换个说法，或确认相关文档是否已上传。"
            return

        # 3. 构建消息
        user_content = RAG_USER_TEMPLATE.format(context=context, question=question)
        messages = [
            ChatMessage(role="system", content=RAG_SYSTEM_PROMPT),
            ChatMessage(role="user", content=user_content),
        ]

        # 4. 流式调用 LLM
        async for chunk in self._llm.chat_stream(messages):
            yield chunk
