"""基于版本化题库的确定性 RAG 回归评测。"""

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from loguru import logger

from app.service.rag_service import RAGResponse, RAGService

DEFAULT_DATASET_PATH = Path(__file__).resolve().parents[2] / "docs" / "evaluation" / "cases.jsonl"


class RAGQueryClient(Protocol):
    """评测所需的最小 RAG 查询契约，便于单元测试替换真实服务。"""

    async def query(self, question: str) -> RAGResponse:
        """执行一次 RAG 问答。"""


@dataclass(frozen=True)
class DatasetCase:
    """版本化评测题库中的单条用例。"""

    case_id: str
    category: str
    question: str
    expected_sources: list[str]
    expected_answer_points: list[str]
    should_refuse: bool


@dataclass
class DatasetCaseResult:
    """单条题库用例的确定性评测结果。"""

    case_id: str
    category: str
    question: str
    should_refuse: bool
    answer_status: str
    answer: str
    expected_sources: list[str]
    actual_sources: list[str]
    source_recall: float
    source_precision: float
    source_exact_match: bool
    answer_point_coverage: float
    refusal_correct: bool
    latency_ms: float

    def to_dict(self) -> dict:
        """转换为 API 可直接返回的字典。"""
        return asdict(self)


@dataclass
class DatasetEvaluationSummary:
    """一次题库回归评测的聚合结果。"""

    dataset_path: str
    total: int
    knowledge_cases: int
    refusal_cases: int
    source_exact_match_rate: float
    source_recall: float
    source_precision: float
    answer_point_coverage: float
    refusal_accuracy: float
    average_latency_ms: float
    results: list[DatasetCaseResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        """转换为 API 可直接返回的字典。"""
        payload = asdict(self)
        payload["results"] = [result.to_dict() for result in self.results]
        return payload


class DatasetEvalService:
    """读取 JSONL 题库并执行无缓存的 RAG 回归评测。"""

    def __init__(self, dataset_path: Path | None = None, rag_service: RAGQueryClient | None = None) -> None:
        self._dataset_path = dataset_path or DEFAULT_DATASET_PATH
        # 回归评测必须绕过语义缓存，否则无法验证真实的检索来源。
        self._rag_service = rag_service or RAGService(use_semantic_cache=False)

    @property
    def dataset_path(self) -> Path:
        """Return the dataset path so a run can persist its immutable content checksum."""
        return self._dataset_path

    def load_cases(self) -> list[DatasetCase]:
        """加载并校验 JSONL 题库。"""
        if not self._dataset_path.is_file():
            raise FileNotFoundError(f"评测题库不存在: {self._dataset_path}")

        cases: list[DatasetCase] = []
        for line_number, line in enumerate(self._dataset_path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                case = DatasetCase(
                    case_id=str(raw["id"]),
                    category=str(raw["category"]),
                    question=str(raw["question"]),
                    expected_sources=list(raw.get("expected_sources", [])),
                    expected_answer_points=list(raw.get("expected_answer_points", [])),
                    should_refuse=bool(raw["should_refuse"]),
                )
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError(f"评测题库第 {line_number} 行格式不合法") from exc
            cases.append(case)

        if not cases:
            raise ValueError("评测题库为空")
        return cases

    async def evaluate(self, limit: int | None = None) -> DatasetEvaluationSummary:
        """顺序执行题库并计算确定性指标，避免压垮外部模型限流。"""
        cases = self.load_cases()
        selected_cases = cases[:limit] if limit else cases
        results: list[DatasetCaseResult] = []

        for index, case in enumerate(selected_cases, start=1):
            logger.info("[DatasetEval] 执行 {}/{} | case_id={}", index, len(selected_cases), case.case_id)
            started_at = time.perf_counter()
            response = await self._rag_service.query(case.question)
            latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
            results.append(self._evaluate_case(case, response, latency_ms))

        return self._summarize(results)

    @staticmethod
    def _evaluate_case(case: DatasetCase, response: RAGResponse, latency_ms: float) -> DatasetCaseResult:
        """按来源、关键事实与拒答约束计算单条用例结果。"""
        actual_sources = sorted(
            {
                str(source.get("source", ""))
                for source in response.sources
                if source.get("source") and source.get("source") != "semantic_cache"
            }
        )
        expected_sources = sorted(set(case.expected_sources))
        source_intersection = set(actual_sources) & set(expected_sources)

        if expected_sources:
            source_recall = len(source_intersection) / len(expected_sources)
        else:
            source_recall = 1.0 if not actual_sources else 0.0
        source_precision = len(source_intersection) / len(actual_sources) if actual_sources else 0.0
        source_exact_match = set(actual_sources) == set(expected_sources)

        answer_lower = response.answer.lower()
        if case.expected_answer_points:
            covered_points = sum(point.lower() in answer_lower for point in case.expected_answer_points)
            answer_point_coverage = covered_points / len(case.expected_answer_points)
        else:
            answer_point_coverage = 1.0 if response.answer_status == "INSUFFICIENT_EVIDENCE" else 0.0

        refusal_correct = (
            response.answer_status == "INSUFFICIENT_EVIDENCE" and not actual_sources
            if case.should_refuse
            else False
        )
        return DatasetCaseResult(
            case_id=case.case_id,
            category=case.category,
            question=case.question,
            should_refuse=case.should_refuse,
            answer_status=response.answer_status,
            answer=response.answer,
            expected_sources=expected_sources,
            actual_sources=actual_sources,
            source_recall=round(source_recall, 4),
            source_precision=round(source_precision, 4),
            source_exact_match=source_exact_match,
            answer_point_coverage=round(answer_point_coverage, 4),
            refusal_correct=refusal_correct,
            latency_ms=latency_ms,
        )

    def _summarize(self, results: list[DatasetCaseResult]) -> DatasetEvaluationSummary:
        """汇总题库回归指标。"""
        knowledge_results = [result for result in results if not result.should_refuse]
        refusal_results = [result for result in results if result.should_refuse]

        def average(values: list[float]) -> float:
            return round(sum(values) / len(values), 4) if values else 0.0

        return DatasetEvaluationSummary(
            dataset_path=str(self._dataset_path.name),
            total=len(results),
            knowledge_cases=len(knowledge_results),
            refusal_cases=len(refusal_results),
            source_exact_match_rate=average([float(result.source_exact_match) for result in knowledge_results]),
            source_recall=average([result.source_recall for result in knowledge_results]),
            source_precision=average([result.source_precision for result in knowledge_results]),
            answer_point_coverage=average([result.answer_point_coverage for result in knowledge_results]),
            refusal_accuracy=average([float(result.refusal_correct) for result in refusal_results]),
            average_latency_ms=average([result.latency_ms for result in results]),
            results=results,
        )
