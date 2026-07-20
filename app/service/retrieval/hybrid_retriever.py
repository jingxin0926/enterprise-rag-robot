"""
混合检索器（Hybrid Retrieval）

核心策略：
1. 多路召回：BM25（关键词匹配）+ Vector（语义匹配）
2. RRF 融合：Reciprocal Rank Fusion 合并排序
3. Rerank 精排：Cross-Encoder 对 Top 结果重排

为什么需要混合检索：
- 纯向量检索：擅长语义相似，但对专有名词/数字不敏感
- 纯 BM25：擅长精确匹配关键词，但不理解语义
- 混合 + Rerank：两者互补 + 精排 = 最佳效果
"""

from dataclasses import dataclass, field

from loguru import logger

from app.infra.vector.qdrant_store import QdrantStore, SearchResult, get_qdrant_store
from app.service.retrieval.bm25_retriever import BM25Result, BM25Retriever
from app.service.retrieval.reranker import Reranker


@dataclass
class HybridResult:
    """混合检索结果（单条）"""

    content: str  # 检索到的文档片段内容
    score: float  # 相关性分数（RRF融合后）
    metadata: dict = field(default_factory=dict)  # 元数据（来源文件名、片段索引等）
    source_type: str = ""  # 检索来源："vector"(向量) / "bm25"(关键词) / "hybrid"(融合)
    vector_score: float | None = None  # 原始向量相似度，用于证据门禁
    bm25_score: float | None = None  # 原始 BM25 分数，用于判断双路一致性
    rrf_score: float | None = None  # RRF 融合分，仅用于排序而非置信度判断


class HybridRetriever:
    """
    混合检索器

    整合 BM25 + 向量检索 + RRF 融合 + Rerank 重排（可选）

    用法：
        retriever = HybridRetriever()
        retriever.add_documents(texts, metadatas)
        results = retriever.search("问题", top_k=5)
    """

    def __init__(
        self,
        collection_name: str | None = None,
        use_rerank: bool = False,  # 暂时默认关闭，后续引入专用 rerank 模型
        vector_weight: float = 0.6,  # 向量检索权重（融合时）
        bm25_weight: float = 0.4,  # BM25 权重
    ) -> None:
        self._collection_name = collection_name
        self._vector_store: QdrantStore = get_qdrant_store(collection_name)
        self._bm25 = BM25Retriever()
        self._reranker = Reranker() if use_rerank else None
        self._vector_weight = vector_weight
        self._bm25_weight = bm25_weight

    def add_documents(self, texts: list[str], metadatas: list[dict] | None = None) -> None:
        """
        同时添加文档到向量库和 BM25 索引

        注意：向量库已在 upload 时写入，这里主要同步 BM25
        """
        self._bm25.add_documents(texts, metadatas)

    def remove_by_document_id(self, document_id: str) -> int:
        """从关键词索引移除已删除文档的切片。"""
        return self._bm25.remove_by_document_id(document_id)

    def _rrf_fusion(
        self,
        vector_results: list[SearchResult],
        bm25_results: list[BM25Result],
        k: int = 60,
    ) -> list[HybridResult]:
        """
        RRF (Reciprocal Rank Fusion) 融合排序

        公式：RRF_score = sum(1 / (k + rank_i)) for each list

        Args:
            vector_results: 向量检索结果
            bm25_results: BM25 检索结果
            k: RRF 常数（默认 60，业界标准值）

        Returns:
            融合后的结果（按 RRF 分数降序）
        """
        # 用 content 作为去重键
        scores: dict[str, float] = {}
        content_map: dict[str, dict] = {}  # content -> metadata / 多路原始分数

        # 向量检索贡献
        for rank, result in enumerate(vector_results):
            content = result.content
            rrf_score = self._vector_weight / (k + rank + 1)
            scores[content] = scores.get(content, 0) + rrf_score
            details = content_map.setdefault(
                content,
                {"metadata": result.metadata, "vector_score": None, "bm25_score": None},
            )
            details["vector_score"] = result.score

        # BM25 贡献
        for rank, result in enumerate(bm25_results):
            content = result.content
            rrf_score = self._bm25_weight / (k + rank + 1)
            scores[content] = scores.get(content, 0) + rrf_score
            details = content_map.setdefault(
                content,
                {"metadata": result.metadata, "vector_score": None, "bm25_score": None},
            )
            details["bm25_score"] = result.score

        # 按融合分数排序
        sorted_items = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        results = []
        for content, score in sorted_items:
            details = content_map[content]
            results.append(
                HybridResult(
                    content=content,
                    score=score,
                    metadata=details["metadata"],
                    source_type="hybrid",
                    vector_score=details["vector_score"],
                    bm25_score=details["bm25_score"],
                    rrf_score=score,
                )
            )

        return results

    def search(
        self,
        query: str,
        top_k: int = 5,
        vector_top_k: int = 10,
        bm25_top_k: int = 10,
        rerank_top_k: int = 5,
        score_threshold: float = 0.3,
    ) -> list[HybridResult]:
        """
        混合检索

        流程：BM25 + Vector 并行 → RRF 融合 → Rerank 精排 → Top-K

        Args:
            query: 查询文本
            top_k: 最终返回条数
            vector_top_k: 向量检索召回条数
            bm25_top_k: BM25 召回条数
            rerank_top_k: 送入 Rerank 的条数
            score_threshold: 向量检索最低阈值

        Returns:
            HybridResult 列表
        """
        # 1. 双路召回
        vector_results = self._vector_store.search(
            query=query,
            top_k=vector_top_k,
            score_threshold=score_threshold,
        )
        bm25_results = self._bm25.search(query=query, top_k=bm25_top_k)

        logger.info(
            "[Hybrid] 双路召回 | vector_hits={} bm25_hits={}",
            len(vector_results),
            len(bm25_results),
        )

        # 2. RRF 融合
        fused = self._rrf_fusion(vector_results, bm25_results)

        # 如果没有结果，直接返回
        if not fused:
            return []

        # 3. Rerank 精排（如果启用）
        if self._reranker and len(fused) > 1:
            candidates = fused[: rerank_top_k * 2]  # 多取一些送去精排
            candidate_by_content = {candidate.content: candidate for candidate in candidates}
            reranked = self._reranker.rerank(
                query=query,
                documents=[r.content for r in candidates],
                metadatas=[r.metadata for r in candidates],
                top_k=top_k,
            )
            results = [
                HybridResult(
                    content=r.content,
                    score=r.score,
                    metadata=r.metadata,
                    source_type="hybrid+rerank",
                    vector_score=candidate_by_content[r.content].vector_score,
                    bm25_score=candidate_by_content[r.content].bm25_score,
                    rrf_score=candidate_by_content[r.content].rrf_score,
                )
                for r in reranked
            ]
        else:
            results = fused[:top_k]

        logger.info(
            "[Hybrid] 检索完成 | query='{}...' final_hits={}",
            query[:20],
            len(results),
        )
        return results


# 按租户缓存实例（每个租户独立的混合检索器）
_hybrid_retrievers: dict[str, HybridRetriever] = {}


def get_hybrid_retriever(collection_name: str | None = None) -> HybridRetriever:
    """
    获取混合检索器实例（按租户隔离）

    不传 collection_name 时，自动使用当前请求的租户 collection。
    """
    from app.core.tenant import get_tenant_collection_name

    name = collection_name or get_tenant_collection_name()
    if name not in _hybrid_retrievers:
        _hybrid_retrievers[name] = HybridRetriever(collection_name=name)
    return _hybrid_retrievers[name]
