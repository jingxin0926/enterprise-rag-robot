"""Evaluation history serialization and privacy boundary tests."""

import hashlib
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import PROJECT_ROOT
from app.service.dataset_eval_service import DatasetEvaluationSummary
from app.service.evaluation_history_service import EvaluationHistoryService


def test_evaluation_run_metadata_versions_dataset_without_persisting_secrets() -> None:
    """A persisted run records dataset/config versions but must never include credentials."""
    dataset_path = PROJECT_ROOT / "tests" / "fixtures" / "evaluation_cases.jsonl"
    service = EvaluationHistoryService.__new__(EvaluationHistoryService)
    service._evaluator = SimpleNamespace(dataset_path=dataset_path)

    metadata = service._build_run_metadata("run-001", "tenant-a", "admin-001")

    assert metadata["id"] == "run-001"
    assert metadata["tenant_id"] == "tenant-a"
    assert metadata["dataset_checksum"] == hashlib.sha256(dataset_path.read_bytes()).hexdigest()
    assert "DEEPSEEK" not in metadata["retrieval_config"]
    assert "QDRANT_API_KEY" not in metadata["retrieval_config"]


def test_evaluation_history_serializes_database_values_for_json_api() -> None:
    """Database datetime/decimal/JSON values must be safe for the unified API response."""
    record = EvaluationHistoryService._serialize_record(
        {
            "source_recall": Decimal("0.6840"),
            "create_time": datetime(2026, 7, 21, 10, 30, 0),
            "retrieval_config": '{"context_neighbor_window": 1}',
        }
    )
    detail = EvaluationHistoryService._serialize_case_result(
        {
            "latency_ms": Decimal("2955.22"),
            "expected_sources": '["图片作品改造.md"]',
            "actual_sources": '["图片作品改造.md"]',
        }
    )

    assert record["source_recall"] == 0.684
    assert record["create_time"] == "2026-07-21T10:30:00"
    assert record["retrieval_config"]["context_neighbor_window"] == 1
    assert detail["latency_ms"] == 2955.22
    assert detail["expected_sources"] == ["图片作品改造.md"]


def test_evaluation_history_migration_contains_run_and_case_tables() -> None:
    """Evaluation history needs append-only run records and case-level traceability."""
    migration = (PROJECT_ROOT / "db" / "migrations" / "V003__rag_evaluation_history.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS rag_eval_run" in migration
    assert "CREATE TABLE IF NOT EXISTS rag_eval_case_result" in migration
    assert "idx_eval_run_tenant_created" in migration
    assert "uk_eval_case_run_case" in migration


@pytest.mark.asyncio
async def test_evaluation_execution_persists_completed_run_and_audit_log(monkeypatch: pytest.MonkeyPatch) -> None:
    """Successful execution must create a run, persist its summary, and write an audit record."""
    dataset_path = PROJECT_ROOT / "tests" / "fixtures" / "evaluation_cases.jsonl"
    summary = DatasetEvaluationSummary(
        dataset_path=dataset_path.name,
        total=2,
        knowledge_cases=1,
        refusal_cases=1,
        source_exact_match_rate=1.0,
        source_recall=1.0,
        source_precision=1.0,
        answer_point_coverage=1.0,
        refusal_accuracy=1.0,
        average_latency_ms=100.0,
    )
    evaluator = SimpleNamespace(dataset_path=dataset_path, evaluate=AsyncMock(return_value=summary))
    service = EvaluationHistoryService(evaluator=evaluator)

    class FakeSessionScope:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

    create_run = AsyncMock()
    complete_run = AsyncMock()
    write_audit_log = AsyncMock()
    monkeypatch.setattr("app.service.evaluation_history_service.session_scope", lambda: FakeSessionScope())
    monkeypatch.setattr("app.service.evaluation_history_service.EvaluationRepository.create_run", create_run)
    monkeypatch.setattr("app.service.evaluation_history_service.EvaluationRepository.complete_run", complete_run)
    monkeypatch.setattr("app.service.evaluation_history_service.KnowledgeRepository.write_audit_log", write_audit_log)

    payload = await service.execute(tenant_id="tenant-a", operator_id="admin-001", limit=2)

    assert payload["total"] == 2
    evaluator.evaluate.assert_awaited_once_with(limit=2)
    create_run.assert_awaited_once()
    complete_run.assert_awaited_once()
    write_audit_log.assert_awaited_once()
