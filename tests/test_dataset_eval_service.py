"""版本化 RAG 题库评测测试。"""

import pytest

from app.core.config import PROJECT_ROOT
from app.service.dataset_eval_service import DatasetEvalService
from app.service.rag_service import RAGResponse


class FakeRAGService:
    """返回预置响应，避免测试依赖模型和向量库。"""

    def __init__(self, responses: dict[str, RAGResponse]) -> None:
        self._responses = responses

    async def query(self, question: str) -> RAGResponse:
        """按题目返回预置 RAG 响应。"""
        return self._responses[question]


@pytest.mark.asyncio
async def test_dataset_eval_calculates_source_and_refusal_metrics() -> None:
    """题库评测应分别统计知识问答来源命中与无依据拒答。"""
    dataset = PROJECT_ROOT / "tests" / "fixtures" / "evaluation_cases.jsonl"
    rag = FakeRAGService(
        {
            "创建接口是什么？": RAGResponse(
                answer="使用 POST /pms/demand/create 创建需求。",
                sources=[{"source": "需求.md", "score": 0.8}],
                evidence_count=1,
            ),
            "年假几天？": RAGResponse(
                answer="抱歉，知识库中暂未找到相关内容。",
                sources=[],
                answer_status="INSUFFICIENT_EVIDENCE",
            ),
        }
    )

    summary = await DatasetEvalService(dataset_path=dataset, rag_service=rag).evaluate()

    assert summary.total == 2
    assert summary.source_exact_match_rate == 1.0
    assert summary.source_recall == 1.0
    assert summary.answer_point_coverage == 1.0
    assert summary.refusal_accuracy == 1.0
