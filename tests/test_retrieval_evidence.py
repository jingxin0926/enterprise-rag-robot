"""混合检索证据溯源与可信回答门禁测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.infra.vector.qdrant_store import QdrantStore, SearchResult
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


def test_multi_query_fusion_deduplicates_hits_and_preserves_best_raw_scores() -> None:
    """同一片段命中原始问法和改写问法时应提升排序且保留最强原始分数。"""
    rag_service = RAGService.__new__(RAGService)
    rag_service._top_k = 5

    merged = rag_service._merge_multi_query_results(
        [
            [
                HybridResult(content="共享片段", score=0.03, vector_score=0.61, bm25_score=0.8, source_type="hybrid"),
                HybridResult(content="原始问法片段", score=0.02, vector_score=0.75, source_type="hybrid"),
            ],
            [
                HybridResult(content="共享片段", score=0.02, vector_score=0.67, bm25_score=1.4, source_type="hybrid"),
                HybridResult(content="改写问法片段", score=0.02, bm25_score=1.2, source_type="hybrid"),
            ],
        ]
    )

    assert [item.content for item in merged] == ["共享片段", "原始问法片段", "改写问法片段"]
    assert merged[0].vector_score == 0.67
    assert merged[0].bm25_score == 1.4
    assert merged[0].source_type == "hybrid+multi_query"


@pytest.mark.asyncio
async def test_retrieval_searches_original_and_rewritten_query() -> None:
    """改写不可替代原始关键词，两个问法都必须参与召回。"""
    rag_service = RAGService.__new__(RAGService)
    rag_service._rewriter = Mock(rewrite=AsyncMock(return_value="图片逐张播放时长限制"))
    rag_service._hybrid = Mock()
    rag_service._hybrid.search.side_effect = [
        [HybridResult(content="原始关键词片段", score=0.03, vector_score=0.74, source_type="hybrid")],
        [HybridResult(content="改写关键词片段", score=0.03, bm25_score=1.3, source_type="hybrid")],
    ]
    rag_service._score_threshold = 0.3
    rag_service._top_k = 5

    results, rewritten = await rag_service._retrieve("图片合成视频时每张图片停留时长限制")

    assert rewritten == "图片逐张播放时长限制"
    assert [item.content for item in results] == ["原始关键词片段", "改写关键词片段"]
    assert [call.kwargs["query"] for call in rag_service._hybrid.search.call_args_list] == [
        "图片合成视频时每张图片停留时长限制",
        "图片逐张播放时长限制",
    ]


def test_context_expansion_adds_only_adjacent_chunks_from_same_document() -> None:
    """局部扩展应补齐命中片段相邻内容，但不能改变引用来源或重复其他原始命中。"""

    class FakeVectorStore:
        def get_neighbor_chunks(self, document_id: str, chunk_index: int, window: int) -> list[SearchResult]:
            assert (document_id, chunk_index, window) == ("doc-image", 3, 1)
            return [
                SearchResult(
                    content="相邻片段中的 duration_seconds 字段说明",
                    score=0.0,
                    metadata={"source": "图片作品改造.md", "document_id": "doc-image", "chunk_index": 2},
                ),
                SearchResult(
                    content="已命中的主片段",
                    score=0.0,
                    metadata={"source": "图片作品改造.md", "document_id": "doc-image", "chunk_index": 3},
                ),
            ]

    rag_service = RAGService.__new__(RAGService)
    rag_service._vector_store = FakeVectorStore()
    rag_service._context_neighbor_window = 1
    rag_service._context_max_neighbor_chunks = 6

    context, sources = rag_service._build_context(
        [
            HybridResult(
                content="已命中的主片段",
                score=0.02,
                metadata={"source": "图片作品改造.md", "document_id": "doc-image", "chunk_index": 3},
                source_type="hybrid",
            )
        ]
    )

    assert "已命中的主片段" in context
    assert "duration_seconds" in context
    assert "同文档相邻片段" in context
    assert [source["source"] for source in sources] == ["图片作品改造.md"]


def test_qdrant_neighbor_lookup_is_scoped_to_one_document_and_chunk_window() -> None:
    """Qdrant 相邻片段查询必须同时限制文档 ID 和片段索引范围。"""
    store = QdrantStore.__new__(QdrantStore)
    store._collection_name = "tenant_t_default_knowledge"
    store._client = Mock()
    store._client.scroll.return_value = (
        [
            SimpleNamespace(payload={"content": "第三段", "chunk_index": 3}),
            SimpleNamespace(payload={"content": "第二段", "chunk_index": 2}),
        ],
        None,
    )

    chunks = store.get_neighbor_chunks(document_id="doc-image", chunk_index=3, window=1)

    assert [chunk.content for chunk in chunks] == ["第二段", "第三段"]
    kwargs = store._client.scroll.call_args.kwargs
    conditions = kwargs["scroll_filter"].must
    assert conditions[0].match.value == "doc-image"
    assert conditions[1].range.gte == 2
    assert conditions[1].range.lte == 4
