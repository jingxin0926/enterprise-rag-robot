"""RAG regression evaluation persistence repository."""

import json
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.service.dataset_eval_service import DatasetEvaluationSummary


class EvaluationRepository:
    """Keep evaluation calculation separate from MySQL persistence concerns."""

    @staticmethod
    async def create_run(session: AsyncSession, run: dict[str, Any]) -> None:
        """Create a RUNNING evaluation record before invoking the model."""
        await session.execute(
            text(
                """
                INSERT INTO rag_eval_run(
                    id, tenant_id, dataset_name, dataset_checksum, app_version, git_commit,
                    retrieval_config, status, executed_by
                ) VALUES (
                    :id, :tenant_id, :dataset_name, :dataset_checksum, :app_version, :git_commit,
                    CAST(:retrieval_config AS JSON), 'RUNNING', :executed_by
                )
                """
            ),
            run,
        )

    @staticmethod
    async def complete_run(session: AsyncSession, run_id: str, summary: DatasetEvaluationSummary) -> None:
        """Save aggregate metrics and per-case results in one transaction."""
        await session.execute(
            text(
                """
                UPDATE rag_eval_run
                SET status = 'COMPLETED', total = :total, knowledge_cases = :knowledge_cases,
                    refusal_cases = :refusal_cases, source_exact_match_rate = :source_exact_match_rate,
                    source_recall = :source_recall, source_precision = :source_precision,
                    answer_point_coverage = :answer_point_coverage, refusal_accuracy = :refusal_accuracy,
                    average_latency_ms = :average_latency_ms, finished_at = NOW(), error_message = ''
                WHERE id = :run_id AND deleted = 0
                """
            ),
            {"run_id": run_id, **summary.to_dict()},
        )
        for result in summary.results:
            await session.execute(
                text(
                    """
                    INSERT INTO rag_eval_case_result(
                        id, run_id, case_id, category, question, should_refuse, answer_status, answer,
                        expected_sources, actual_sources, source_recall, source_precision, source_exact_match,
                        answer_point_coverage, refusal_correct, latency_ms
                    ) VALUES (
                        :id, :run_id, :case_id, :category, :question, :should_refuse, :answer_status, :answer,
                        CAST(:expected_sources AS JSON), CAST(:actual_sources AS JSON), :source_recall,
                        :source_precision, :source_exact_match, :answer_point_coverage, :refusal_correct, :latency_ms
                    )
                    """
                ),
                {
                    "id": str(uuid4()),
                    "run_id": run_id,
                    "case_id": result.case_id,
                    "category": result.category,
                    "question": result.question,
                    "should_refuse": result.should_refuse,
                    "answer_status": result.answer_status,
                    "answer": result.answer,
                    "expected_sources": json.dumps(result.expected_sources, ensure_ascii=False),
                    "actual_sources": json.dumps(result.actual_sources, ensure_ascii=False),
                    "source_recall": result.source_recall,
                    "source_precision": result.source_precision,
                    "source_exact_match": result.source_exact_match,
                    "answer_point_coverage": result.answer_point_coverage,
                    "refusal_correct": result.refusal_correct,
                    "latency_ms": result.latency_ms,
                },
            )

    @staticmethod
    async def fail_run(session: AsyncSession, run_id: str, error_message: str) -> None:
        """Keep failed runs auditable without persisting partial case results."""
        await session.execute(
            text(
                """
                UPDATE rag_eval_run
                SET status = 'FAILED', finished_at = NOW(), error_message = :error_message
                WHERE id = :run_id AND deleted = 0
                """
            ),
            {"run_id": run_id, "error_message": error_message[:1000]},
        )

    @staticmethod
    async def list_runs(session: AsyncSession, tenant_id: str, limit: int) -> list[dict[str, Any]]:
        """Return recent runs scoped to one tenant."""
        result = await session.execute(
            text(
                """
                SELECT id, dataset_name, dataset_checksum, app_version, git_commit, retrieval_config, status,
                       total, knowledge_cases, refusal_cases, source_exact_match_rate, source_recall,
                       source_precision, answer_point_coverage, refusal_accuracy, average_latency_ms,
                       executed_by, error_message, started_at, finished_at, create_time
                FROM rag_eval_run
                WHERE tenant_id = :tenant_id AND deleted = 0
                ORDER BY create_time DESC
                LIMIT :limit
                """
            ),
            {"tenant_id": tenant_id, "limit": limit},
        )
        return [dict(row) for row in result.mappings().all()]

    @staticmethod
    async def get_run(session: AsyncSession, tenant_id: str, run_id: str) -> dict[str, Any] | None:
        """Read one run while enforcing tenant ownership."""
        result = await session.execute(
            text(
                """
                SELECT id, dataset_name, dataset_checksum, app_version, git_commit, retrieval_config, status,
                       total, knowledge_cases, refusal_cases, source_exact_match_rate, source_recall,
                       source_precision, answer_point_coverage, refusal_accuracy, average_latency_ms,
                       executed_by, error_message, started_at, finished_at, create_time
                FROM rag_eval_run
                WHERE id = :run_id AND tenant_id = :tenant_id AND deleted = 0
                """
            ),
            {"run_id": run_id, "tenant_id": tenant_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    @staticmethod
    async def list_case_results(session: AsyncSession, run_id: str) -> list[dict[str, Any]]:
        """Read all case results for one completed run."""
        result = await session.execute(
            text(
                """
                SELECT case_id, category, question, should_refuse, answer_status, answer,
                       expected_sources, actual_sources, source_recall, source_precision, source_exact_match,
                       answer_point_coverage, refusal_correct, latency_ms
                FROM rag_eval_case_result
                WHERE run_id = :run_id AND deleted = 0
                ORDER BY create_time ASC
                """
            ),
            {"run_id": run_id},
        )
        return [dict(row) for row in result.mappings().all()]
