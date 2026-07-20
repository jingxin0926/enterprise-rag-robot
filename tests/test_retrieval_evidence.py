"""混合检索证据溯源与可信回答门禁测试。"""

from app.infra.vector.qdrant_store import SearchResult
from app.service.rag_service import RAGService
from app.service.retrieval.bm25_retriever import BM25Result
from app.service.retrieval.hybrid_retriever import HybridResult, HybridRetriever


def test_rrf_fusion_preserves_raw_retrieval_scores() -> None:
    """RRF 融合后必须保留原始分数，门禁不能依赖不可解释的融合分。"""
    retriever = HybridRetriever.__new__(HybridRetriever)
    retriever._vector_weight = 0.6
    retriever._bm25_weight = 0.4

    results = retriever._rrf_fusion(
        [SearchResult(content="证据片段", score=0.72, metadata={"source": "需求.md"})],
        [BM25Result(content="证据片段", score=4.2, index=0, metadata={"source": "需求.md"})],
    )

    assert len(results) == 1
    assert results[0].vector_score == 0.72
    assert results[0].bm25_score == 4.2
    assert results[0].rrf_score == results[0].score


def test_evidence_gate_requires_quality_bm25_or_strong_vector() -> None:
    """弱 BM25 通用词命中不能成为证据，高质量双路和强向量候选可以。"""
    rag_service = RAGService.__new__(RAGService)
    rag_service._strong_vector_score = 0.72
    rag_service._minimum_bm25_score = 1.0

    accepted = rag_service._filter_sufficient_evidence(
        [
            HybridResult(content="弱单路", score=0.01, vector_score=0.42),
            HybridResult(content="通用词双路", score=0.02, vector_score=0.44, bm25_score=0.3),
            HybridResult(content="高质量双路", score=0.02, vector_score=0.44, bm25_score=1.1),
            HybridResult(content="强向量", score=0.01, vector_score=0.75),
        ]
    )

    assert [item.content for item in accepted] == ["高质量双路", "强向量"]
