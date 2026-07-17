"""Qdrant 历史向量元数据回填服务。"""

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from app.core.tenant import get_tenant_collection_name
from app.infra.database.database import session_scope
from app.infra.vector.qdrant_store import get_qdrant_store
from app.repository.knowledge_repository import KnowledgeRepository


@dataclass
class LegacyBackfillResult:
    """一次历史回填的汇总结果。"""

    documents_created: int = 0
    chunks_backfilled: int = 0
    skipped_chunks: int = 0


class LegacyBackfillService:
    """把缺少 document_id 的既有向量转换为可治理的文档元数据。"""

    async def backfill(self, *, tenant_id: str, operator_id: str, trace_id: str) -> LegacyBackfillResult:
        """执行幂等回填，不重新生成 embedding，也不删除已有向量。"""
        collection_name = get_tenant_collection_name(tenant_id)
        store = get_qdrant_store(collection_name)
        points = store.list_legacy_points()
        if not points:
            return LegacyBackfillResult()

        grouped = self.group_by_source(points)

        result = LegacyBackfillResult(skipped_chunks=0)
        assignments: list[tuple[list[str], str]] = []
        async with session_scope() as session:
            knowledge_base_id = await KnowledgeRepository.ensure_default_knowledge_base(session, tenant_id)

            for source, source_points in grouped.items():
                source_points.sort(
                    key=lambda item: (int(item["payload"].get("chunk_index", 0)), item["point_id"])
                )
                document_id = str(uuid5(NAMESPACE_URL, f"smart-qa:legacy:{tenant_id}:{source}"))
                aggregate_content = "".join(str(item["payload"]["content"]) for item in source_points)
                suffix = Path(source).suffix.lower() or ".legacy"
                await KnowledgeRepository.ensure_legacy_document(
                    session,
                    {
                        "id": document_id,
                        "tenant_id": tenant_id,
                        "knowledge_base_id": knowledge_base_id,
                        "file_name": source,
                        "file_extension": suffix,
                        "content_type": "application/x-smart-qa-legacy",
                        "file_size": len(aggregate_content.encode("utf-8")),
                        "checksum": hashlib.sha256(aggregate_content.encode("utf-8")).hexdigest(),
                        "storage_path": "",
                        "status": "LEGACY_COMPLETED",
                        "chunk_count": len(source_points),
                        "created_by": operator_id,
                    },
                )
                await KnowledgeRepository.ensure_legacy_task(
                    session,
                    {
                        "id": str(uuid5(NAMESPACE_URL, f"smart-qa:legacy-task:{document_id}")),
                        "tenant_id": tenant_id,
                        "document_id": document_id,
                    },
                )
                chunk_records = []
                for chunk_index, point in enumerate(source_points):
                    payload = {**point["payload"], "document_id": document_id, "legacy": True}
                    content = str(payload["content"])
                    chunk_records.append(
                        {
                            "id": str(uuid5(NAMESPACE_URL, f"smart-qa:legacy-chunk:{document_id}:{point['point_id']}")),
                            "tenant_id": tenant_id,
                            "document_id": document_id,
                            "chunk_index": chunk_index,
                            "qdrant_point_id": point["point_id"],
                            "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                            "content_length": len(content),
                            "metadata": json.dumps(
                                {key: value for key, value in payload.items() if key != "content"}, ensure_ascii=False
                            ),
                        }
                    )
                await KnowledgeRepository.ensure_legacy_chunks(session, chunk_records)
                await KnowledgeRepository.write_audit_log(
                    session,
                    {
                        "tenant_id": tenant_id,
                        "operator_id": operator_id,
                        "resource_type": "DOCUMENT",
                        "resource_id": document_id,
                        "action": "LEGACY_BACKFILL",
                        "detail": {"source": source, "chunk_count": len(source_points)},
                        "trace_id": trace_id,
                    },
                )
                assignments.append(([item["point_id"] for item in source_points], document_id))
                result.documents_created += 1
                result.chunks_backfilled += len(source_points)

        # MySQL 已提交后才回写 Qdrant。回写失败可安全重试，因元数据 upsert 为幂等操作。
        for point_ids, document_id in assignments:
            store.assign_document_id(point_ids, document_id)

        from app.service.semantic_cache import get_semantic_cache

        get_semantic_cache().clear()
        return result

    @staticmethod
    def group_by_source(points: list[dict]) -> dict[str, list[dict]]:
        """按来源稳定聚合历史切片，便于测试和重复回填。"""
        grouped: dict[str, list[dict]] = defaultdict(list)
        for point in points:
            source = str(point["payload"].get("source") or "历史未命名文档")
            grouped[source].append(point)
        return dict(grouped)
