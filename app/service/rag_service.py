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

from app.core.config import settings
from app.infra.llm.deepseek_client import ChatMessage, get_deepseek_client
from app.infra.vector.qdrant_store import get_qdrant_store
from app.prompts.loader import get_prompt_loader
from app.service.retrieval.evidence_validator import EvidenceValidator, EvidenceVerdict
from app.service.retrieval.hybrid_retriever import HybridResult, get_hybrid_retriever
from app.service.retrieval.query_rewriter import QueryRewriter

# 用户消息模板（将检索结果和问题组装为用户消息）
RAG_USER_TEMPLATE = """【用户问题】
{question}"""


@dataclass
class RAGResponse:
    """RAG 问答响应"""

    answer: str                                          # LLM 生成的回答内容
    sources: list[dict] = field(default_factory=list)    # 引用的来源列表（文件名、分数、片段索引）
    prompt_tokens: int = 0                               # 输入消耗的 token 数
    completion_tokens: int = 0                           # 输出消耗的 token 数
    total_tokens: int = 0                                # 总消耗 token 数
    rewritten_query: str = ""                            # Query 改写后的检索语句
    retrieval_mode: str = "hybrid"                       # 检索模式："vector"(纯向量) / "hybrid"(混合检索)
    answer_status: str = "ANSWERED"                       # ANSWERED / INSUFFICIENT_EVIDENCE / CACHED
    evidence_count: int = 0                               # 参与生成的去重证据片段数
    evidence_verdict: str = "DETERMINISTIC"                # DETERMINISTIC / LLM_VALIDATED / LLM_REJECTED


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
        score_threshold: float | None = None,
        use_hybrid: bool = True,
        use_rewrite: bool = True,
        use_rerank: bool = True,
        use_semantic_cache: bool = True,
    ) -> None:
        self._top_k = top_k
        self._score_threshold = score_threshold if score_threshold is not None else settings.rag_vector_score_threshold
        self._strong_vector_score = settings.rag_strong_vector_score
        self._minimum_bm25_score = settings.rag_min_bm25_score
        self._context_neighbor_window = settings.rag_context_neighbor_window
        self._context_max_neighbor_chunks = settings.rag_context_max_neighbor_chunks
        self._use_hybrid = use_hybrid
        self._use_rewrite = use_rewrite
        self._use_semantic_cache = use_semantic_cache
        self._evidence_validator = EvidenceValidator() if settings.rag_llm_evidence_validator_enabled else None
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
        reserved_contents = {result.content for result in results}
        emitted_contents: set[str] = set()
        neighbor_chunks_added = 0

        for i, r in enumerate(results, 1):
            # 去重（同一段内容不重复引用）
            if r.content in emitted_contents:
                continue
            emitted_contents.add(r.content)

            source_name = r.metadata.get("source", "未知")
            context_parts.append(f"[{i}] (来源: {source_name})\n{r.content}")
            sources.append({
                "source": source_name,
                "score": round(r.score, 4),
                "chunk_index": r.metadata.get("chunk_index", 0),
                "retrieval_type": r.source_type,
                "vector_score": round(r.vector_score, 4) if r.vector_score is not None else None,
                "bm25_score": round(r.bm25_score, 4) if r.bm25_score is not None else None,
                "rrf_score": round(r.rrf_score, 4) if r.rrf_score is not None else None,
            })

            if neighbor_chunks_added >= self._context_max_neighbor_chunks:
                continue
            document_id = r.metadata.get("document_id")
            chunk_index = r.metadata.get("chunk_index")
            if not document_id or chunk_index is None:
                continue

            try:
                neighbors = self._vector_store.get_neighbor_chunks(
                    document_id=str(document_id),
                    chunk_index=int(chunk_index),
                    window=self._context_neighbor_window,
                )
            except (TypeError, ValueError) as exc:
                logger.warning("[RAG-ContextExpansion] 忽略非法文档片段元数据 | error={}", exc)
                continue
            except Exception as exc:
                # 上下文补全失败不能阻断主检索链路，仍使用原始命中继续完成问答。
                logger.warning("[RAG-ContextExpansion] 相邻片段读取失败，降级为原始命中 | error={}", exc)
                continue

            for neighbor in neighbors:
                if neighbor_chunks_added >= self._context_max_neighbor_chunks:
                    break
                if neighbor.content in reserved_contents or neighbor.content in emitted_contents:
                    continue
                emitted_contents.add(neighbor.content)
                neighbor_chunks_added += 1
                neighbor_source = neighbor.metadata.get("source", source_name)
                neighbor_index = neighbor.metadata.get("chunk_index", "?")
                context_parts.append(
                    f"[{i}.{neighbor_chunks_added}] (同文档相邻片段 | 来源: {neighbor_source} | 片段: {neighbor_index})\n"
                    f"{neighbor.content}"
                )

        logger.info(
            "[RAG-ContextExpansion] base_hits={} neighbor_chunks={} window={}",
            len(sources),
            neighbor_chunks_added,
            self._context_neighbor_window,
        )

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

        if self._hybrid:
            results = self._hybrid.search(
                query=rewritten,
                top_k=self._top_k,
                score_threshold=self._score_threshold,
            )
        else:
            vector_results = self._vector_store.search(
                query=rewritten,
                top_k=self._top_k,
                score_threshold=self._score_threshold,
            )
            results = [
                HybridResult(
                    content=result.content,
                    score=result.score,
                    metadata=result.metadata,
                    source_type="vector",
                    vector_score=result.score,
                )
                for result in vector_results
            ]

        return results, rewritten

    def _filter_sufficient_evidence(self, results: list[HybridResult]) -> list[HybridResult]:
        """只保留双路一致或高语义相似度证据，避免无依据生成。"""
        accepted: list[HybridResult] = []
        candidate_diagnostics: list[dict] = []
        for result in results:
            bm25_qualified = result.bm25_score is not None and result.bm25_score >= self._minimum_bm25_score
            dual_retrieval = result.vector_score is not None and bm25_qualified
            strong_vector = result.vector_score is not None and result.vector_score >= self._strong_vector_score
            if dual_retrieval or strong_vector:
                accepted.append(result)
            candidate_diagnostics.append(
                {
                    "source": result.metadata.get("source", "unknown"),
                    "vector": round(result.vector_score, 4) if result.vector_score is not None else None,
                    "bm25": round(result.bm25_score, 4) if result.bm25_score is not None else None,
                    "accepted": dual_retrieval or strong_vector,
                }
            )

        logger.info(
            "[RAG-EvidenceGate] retrieved={} accepted={} strong_vector_threshold={} min_bm25_score={} candidates={}",
            len(results),
            len(accepted),
            self._strong_vector_score,
            self._minimum_bm25_score,
            candidate_diagnostics,
        )
        return accepted

    async def _validate_evidence(self, question: str, context: str) -> EvidenceVerdict | None:
        """执行可选的第二道 LLM 证据门禁。"""
        if self._evidence_validator is None:
            return None
        return await self._evidence_validator.validate(question, context)

    async def query(self, question: str) -> RAGResponse:
        """RAG 问答（非流式）"""
        import time

        from app.infra.observability.langfuse_client import LLMTracer
        from app.service.semantic_cache import get_semantic_cache

        # 创建追踪
        tracer = LLMTracer(trace_name="rag_query", metadata={"question": question})

        # 0. 先查语义缓存（命中则直接返回，不调 LLM）
        cache = get_semantic_cache()
        cache_hit = cache.lookup(question) if self._use_semantic_cache else None
        if cache_hit:
            tracer.end()
            return RAGResponse(
                answer=cache_hit.answer,
                sources=[{"source": "semantic_cache", "score": cache_hit.score}],
                rewritten_query="",
                retrieval_mode="cache_hit",
                answer_status="CACHED",
            )

        # 1. 检索
        t0 = time.perf_counter()
        results, rewritten = await self._retrieve(question)
        retrieval_ms = (time.perf_counter() - t0) * 1000

        # 记录检索步骤到 Langfuse
        tracer.log_retrieval(
            query=rewritten,
            results=[{"content": r.content[:100], "score": r.score} for r in results[:3]],
            duration_ms=retrieval_ms,
        )

        # 2. 组装上下文
        evidence_results = self._filter_sufficient_evidence(results)
        context, sources = self._build_context(evidence_results)

        if not context:
            tracer.end()
            return RAGResponse(
                answer="抱歉，知识库中暂未找到与您问题相关的内容。请尝试换个说法，或确认相关文档是否已上传。",
                sources=[],
                rewritten_query=rewritten,
                answer_status="INSUFFICIENT_EVIDENCE",
            )

        verdict = await self._validate_evidence(question, context)
        if verdict is not None and not verdict.sufficient:
            tracer.end()
            return RAGResponse(
                answer="抱歉，现有知识库证据不足以可靠回答该问题。请补充相关文档或换一种问法。",
                sources=[],
                rewritten_query=rewritten,
                answer_status="INSUFFICIENT_EVIDENCE",
                evidence_verdict="LLM_REJECTED",
            )

        # 3. 构建消息（从 Prompt 文件加载系统提示词，注入检索上下文）
        system_prompt = get_prompt_loader().load("rag_system", context=context)
        user_content = RAG_USER_TEMPLATE.format(question=question)
        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=user_content),
        ]

        # 4. 调用 LLM
        t1 = time.perf_counter()
        response = await self._llm.chat(messages)
        generation_ms = (time.perf_counter() - t1) * 1000

        # 记录 LLM 生成步骤到 Langfuse
        tracer.log_generation(
            model="deepseek-chat",
            prompt=f"[system]{system_prompt[:200]}...\n[user]{user_content}",
            completion=response.content[:500],
            tokens={
                "input": response.prompt_tokens,
                "output": response.completion_tokens,
                "total": response.total_tokens,
            },
            duration_ms=generation_ms,
        )
        tracer.end()

        # 存入语义缓存（下次相似问题直接命中）
        if self._use_semantic_cache:
            cache.store(question, response.content)

        logger.info(
            "[RAG-P3] 问答完成 | question='{}...' rewritten='{}...' hits={} tokens={}",
            question[:20],
            rewritten[:20],
            len(evidence_results),
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
            evidence_count=len(sources),
            evidence_verdict="LLM_VALIDATED" if verdict is not None else "DETERMINISTIC",
        )

    async def query_stream(self, question: str) -> AsyncGenerator[str, None]:
        """RAG 问答（流式）"""
        # 1. 检索
        results, rewritten = await self._retrieve(question)

        # 2. 组装上下文
        evidence_results = self._filter_sufficient_evidence(results)
        context, sources = self._build_context(evidence_results)

        if not context:
            yield "抱歉，知识库中暂未找到与您问题相关的内容。请尝试换个说法，或确认相关文档是否已上传。"
            return

        verdict = await self._validate_evidence(question, context)
        if verdict is not None and not verdict.sufficient:
            yield "抱歉，现有知识库证据不足以可靠回答该问题。请补充相关文档或换一种问法。"
            return

        # 3. 构建消息（从 Prompt 文件加载系统提示词，注入检索上下文）
        system_prompt = get_prompt_loader().load("rag_system", context=context)
        user_content = RAG_USER_TEMPLATE.format(question=question)
        messages = [
            ChatMessage(role="system", content=system_prompt),
            ChatMessage(role="user", content=user_content),
        ]

        # 4. 流式调用 LLM
        async for chunk in self._llm.chat_stream(messages):
            yield chunk
