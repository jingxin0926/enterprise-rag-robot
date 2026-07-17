"""文档入库生命周期编排。"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from loguru import logger

from app.core.config import PROJECT_ROOT
from app.infra.database.database import session_scope
from app.infra.vector.qdrant_store import get_qdrant_store
from app.repository.knowledge_repository import KnowledgeRepository
from app.service.document_service import DocumentService
from app.service.retrieval.hybrid_retriever import get_hybrid_retriever


@dataclass
class IngestResult:
    """文档入库结果。"""

    document_id: str
    task_id: str
    file_name: str
    chunk_count: int


class DocumentIngestService:
    """管理原文件、处理状态、向量写入和元数据落库。"""

    def __init__(self) -> None:
        self._document_service = DocumentService()

    async def ingest(
        self,
        *,
        tenant_id: str,
        operator_id: str,
        file_name: str,
        content: bytes,
        content_type: str = "",
        trace_id: str = "",
    ) -> IngestResult:
        """同步执行一次入库任务，并完整记录生命周期状态。"""
        document_id = str(uuid4())
        task_id = str(uuid4())
        suffix = Path(file_name).suffix.lower() or ".txt"
        checksum = hashlib.sha256(content).hexdigest()
        storage_path = self._persist_source(tenant_id, document_id, suffix, content)

        async with session_scope() as session:
            knowledge_base_id = await KnowledgeRepository.ensure_default_knowledge_base(session, tenant_id)
            await KnowledgeRepository.create_document(
                session,
                {
                    "id": document_id,
                    "tenant_id": tenant_id,
                    "knowledge_base_id": knowledge_base_id,
                    "file_name": file_name,
                    "file_extension": suffix,
                    "content_type": content_type,
                    "file_size": len(content),
                    "checksum": checksum,
                    "storage_path": str(storage_path),
                    "status": "UPLOADED",
                    "created_by": operator_id,
                },
            )
            await KnowledgeRepository.create_task(
                session,
                {
                    "id": task_id,
                    "tenant_id": tenant_id,
                    "document_id": document_id,
                    "task_type": "INGEST",
                    "status": "PENDING",
                },
            )
            await KnowledgeRepository.write_audit_log(
                session,
                {
                    "tenant_id": tenant_id,
                    "operator_id": operator_id,
                    "resource_type": "DOCUMENT",
                    "resource_id": document_id,
                    "action": "CREATE",
                    "detail": {"file_name": file_name, "task_id": task_id},
                    "trace_id": trace_id,
                },
            )

        point_ids: list[str] = []
        try:
            await self._set_status(document_id, task_id, "PARSING", "RUNNING")
            chunks = self._document_service.parse_and_split(str(storage_path), file_name=file_name)
            if not chunks:
                raise ValueError("文档解析结果为空")

            await self._set_status(document_id, task_id, "CHUNKING", "RUNNING", chunk_count=len(chunks))
            metadatas = [
                {
                    **chunk.metadata,
                    "document_id": document_id,
                    "knowledge_base": "default",
                    "source": file_name,
                }
                for chunk in chunks
            ]
            texts = [chunk.content for chunk in chunks]

            await self._set_status(document_id, task_id, "INDEXING", "RUNNING", chunk_count=len(chunks))
            store = get_qdrant_store()
            point_ids = store.add_documents(texts, metadatas)
            get_hybrid_retriever().add_documents(texts, metadatas)

            records = [
                {
                    "id": str(uuid4()),
                    "tenant_id": tenant_id,
                    "document_id": document_id,
                    "chunk_index": index,
                    "qdrant_point_id": point_ids[index],
                    "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "content_length": len(text),
                    "metadata": json.dumps(metadata, ensure_ascii=False),
                }
                for index, (text, metadata) in enumerate(zip(texts, metadatas, strict=True))
            ]
            async with session_scope() as session:
                await KnowledgeRepository.create_chunks(session, records)
                await KnowledgeRepository.update_document_status(
                    session, document_id, "COMPLETED", chunk_count=len(chunks), error_message=""
                )
                await KnowledgeRepository.update_task_status(session, task_id, "COMPLETED")
                await KnowledgeRepository.write_audit_log(
                    session,
                    {
                        "tenant_id": tenant_id,
                        "operator_id": operator_id,
                        "resource_type": "DOCUMENT",
                        "resource_id": document_id,
                        "action": "INGEST_COMPLETED",
                        "detail": {"chunk_count": len(chunks), "task_id": task_id},
                        "trace_id": trace_id,
                    },
                )

            from app.service.semantic_cache import get_semantic_cache

            get_semantic_cache().clear()
            logger.info("[Document] 入库完成 | document_id={} chunks={}", document_id, len(chunks))
            return IngestResult(document_id=document_id, task_id=task_id, file_name=file_name, chunk_count=len(chunks))
        except Exception as exc:
            if point_ids:
                get_qdrant_store().delete_points(point_ids)
                get_hybrid_retriever().remove_by_document_id(document_id)
            async with session_scope() as session:
                await KnowledgeRepository.update_document_status(
                    session, document_id, "FAILED", error_message=str(exc)[:1000]
                )
                await KnowledgeRepository.update_task_status(session, task_id, "FAILED", error_message=str(exc)[:1000])
            logger.exception("[Document] 入库失败 | document_id={} error={}", document_id, exc)
            raise

    @staticmethod
    def _persist_source(tenant_id: str, document_id: str, suffix: str, content: bytes) -> Path:
        """持久化原文件，使后续重建索引拥有可靠输入。"""
        directory = PROJECT_ROOT / "data" / "uploads" / tenant_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{document_id}{suffix}"
        path.write_bytes(content)
        return path

    @staticmethod
    async def _set_status(
        document_id: str,
        task_id: str,
        document_status: str,
        task_status: str,
        *,
        chunk_count: int | None = None,
    ) -> None:
        """以独立事务保存中间状态，保证故障可观测。"""
        async with session_scope() as session:
            await KnowledgeRepository.update_document_status(
                session, document_id, document_status, chunk_count=chunk_count
            )
            await KnowledgeRepository.update_task_status(session, task_id, task_status)
