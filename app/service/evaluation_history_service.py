"""RAG regression evaluation history service."""

import hashlib
import json
from datetime import date, datetime
from typing import Any
from uuid import uuid4

from app.core.config import settings
from app.infra.database.database import session_scope
from app.repository.evaluation_repository import EvaluationRepository
from app.repository.knowledge_repository import KnowledgeRepository
from app.service.dataset_eval_service import DatasetEvalService


class EvaluationHistoryService:
    """Coordinate evaluation execution, persistence, audit logging, and history queries."""

    def __init__(self, evaluator: DatasetEvalService | None = None) -> None:
        self._evaluator = evaluator or DatasetEvalService()

    async def execute(self, tenant_id: str, operator_id: str, limit: int | None) -> dict[str, Any]:
        """Persist a RUNNING record before execution, then atomically save completed results."""
        run_id = str(uuid4())
        run = self._build_run_metadata(run_id, tenant_id, operator_id)
        async with session_scope() as session:
            await EvaluationRepository.create_run(session, run)

        try:
            summary = await self._evaluator.evaluate(limit=limit)
            async with session_scope() as session:
                await EvaluationRepository.complete_run(session, run_id, summary)
                await KnowledgeRepository.write_audit_log(
                    session,
                    {
                        "tenant_id": tenant_id,
                        "operator_id": operator_id,
                        "resource_type": "RAG_EVALUATION",
                        "resource_id": run_id,
                        "action": "EVAL_COMPLETED",
                        "detail": {"total": summary.total, "dataset": summary.dataset_path},
                        "trace_id": "",
                    },
                )
            return {"run_id": run_id, **summary.to_dict()}
        except Exception as exc:
            async with session_scope() as session:
                await EvaluationRepository.fail_run(session, run_id, str(exc))
            raise

    async def list_runs(self, tenant_id: str, limit: int) -> list[dict[str, Any]]:
        """Return recent runs scoped to the current tenant."""
        async with session_scope() as session:
            runs = await EvaluationRepository.list_runs(session, tenant_id, limit)
        return self._attach_comparisons([self._serialize_record(run) for run in runs])

    async def get_run_detail(self, tenant_id: str, run_id: str) -> dict[str, Any] | None:
        """Return one run with its configuration snapshot and all case results."""
        async with session_scope() as session:
            run = await EvaluationRepository.get_run(session, tenant_id, run_id)
            if run is None:
                return None
            results = await EvaluationRepository.list_case_results(session, run_id)

        payload = self._serialize_record(run)
        payload["results"] = [self._serialize_case_result(result) for result in results]
        return payload

    def _build_run_metadata(self, run_id: str, tenant_id: str, operator_id: str) -> dict[str, str]:
        """Build a versioned, non-sensitive snapshot of the active retrieval configuration."""
        dataset_path = self._evaluator.dataset_path
        config = {
            "vector_score_threshold": settings.rag_vector_score_threshold,
            "strong_vector_score": settings.rag_strong_vector_score,
            "min_bm25_score": settings.rag_min_bm25_score,
            "llm_evidence_validator_enabled": settings.rag_llm_evidence_validator_enabled,
            "context_neighbor_window": settings.rag_context_neighbor_window,
            "context_max_neighbor_chunks": settings.rag_context_max_neighbor_chunks,
        }
        return {
            "id": run_id,
            "tenant_id": tenant_id,
            "dataset_name": dataset_path.name,
            "dataset_checksum": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
            "app_version": settings.app_version,
            "git_commit": settings.app_git_commit,
            "retrieval_config": json.dumps(config, ensure_ascii=False, sort_keys=True),
            "executed_by": operator_id,
        }

    @staticmethod
    def _attach_comparisons(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Compare each completed run with the nearest older comparable baseline.

        The dataset checksum and executed case count must match. This prevents a smoke run
        or a changed dataset from being presented as a quality regression.
        """
        metric_fields = (
            "source_exact_match_rate",
            "source_recall",
            "answer_point_coverage",
            "refusal_accuracy",
            "average_latency_ms",
        )
        for index, current in enumerate(runs):
            if current.get("status") != "COMPLETED":
                current["comparison"] = {"comparable": False, "reason": "运行未完成"}
                continue

            baseline = next(
                (
                    candidate
                    for candidate in runs[index + 1 :]
                    if candidate.get("status") == "COMPLETED"
                    and candidate.get("dataset_checksum") == current.get("dataset_checksum")
                    and candidate.get("total") == current.get("total")
                ),
                None,
            )
            if baseline is None:
                current["comparison"] = {"comparable": False, "reason": "首次基线或执行条数不同"}
                continue

            comparison: dict[str, Any] = {
                "comparable": True,
                "baseline_run_id": baseline["id"],
                "baseline_git_commit": baseline.get("git_commit", "unknown"),
            }
            for field in metric_fields:
                comparison[f"{field}_delta"] = round(float(current[field]) - float(baseline[field]), 4)
            current["comparison"] = comparison
        return runs

    @classmethod
    def _serialize_record(cls, record: dict[str, Any]) -> dict[str, Any]:
        """Normalize JSON, decimal, and datetime values for the API response."""
        payload = {key: cls._serialize_value(value) for key, value in record.items()}
        if isinstance(payload.get("retrieval_config"), str):
            try:
                payload["retrieval_config"] = json.loads(payload["retrieval_config"])
            except json.JSONDecodeError:
                payload["retrieval_config"] = {}
        return payload

    @classmethod
    def _serialize_case_result(cls, result: dict[str, Any]) -> dict[str, Any]:
        """Normalize the per-case JSON columns for the API response."""
        payload = {key: cls._serialize_value(value) for key, value in result.items()}
        for key in ("expected_sources", "actual_sources"):
            if isinstance(payload.get(key), str):
                try:
                    payload[key] = json.loads(payload[key])
                except json.JSONDecodeError:
                    payload[key] = []
        return payload

    @staticmethod
    def _serialize_value(value: Any) -> Any:
        """Convert database-driver values that ORJSON cannot serialize directly."""
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if hasattr(value, "as_tuple"):
            return float(value)
        return value
