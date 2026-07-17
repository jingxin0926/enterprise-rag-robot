"""异步文档入库生命周期编排。"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from loguru import logger

from app.core.config import PROJECT_ROOT, settings
from app.core.tenant import get_tenant_collection_name
from app.infra.database.database import session_scope
from app.infra.vector.qdrant_store import get_qdrant_store
from app.repository.knowledge_repository import KnowledgeRepository
from app.service.document_service import DocumentService
from app.service.retrieval.hybrid_retriever import get_hybrid_retriever


@dataclass
class SubmittedIngestTask:
    """已持久化、等待 Worker 消费的入库任务。"""

    document_id: str
    task_id: str
    file_name: str


@dataclass
class TaskProcessResult:
    """Worker 处理结果。"""

    task_id: str
    completed: bool = False
    should_retry: bool = False
    index_collection: str = ""


class DocumentIngestService:
    """拆分上传提交与 Worker 执行，保证 HTTP 请求不承担重计算。"""

    def __init__(self) -> None:
        self._document_service = DocumentService()

    async def submit(
        self,
        *,
        tenant_id: str,
        operator_id: str,
        file_name: str,
        content: bytes,
        content_type: str = "",
        trace_id: str = "",
    ) -> SubmittedIngestTask:
        """持久化原文件、文档和任务，返回待投递任务。"""
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
                    "status": "PENDING",
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
        logger.info("[Document] 任务已创建 | document_id={} task_id={}", document_id, task_id)
        return SubmittedIngestTask(document_id=document_id, task_id=task_id, file_name=file_name)

    async def process_task(self, task_id: str) -> TaskProcessResult:
        """由 Worker 执行一次文档解析、切分和索引。"""
        async with session_scope() as session:
            task = await KnowledgeRepository.get_task_context(session, task_id)
            if task is None:
                logger.warning("[Document] 任务不存在，跳过 | task_id={}", task_id)
                return TaskProcessResult(task_id=task_id, completed=True)
            if task["task_status"] in {"COMPLETED", "FAILED"}:
                return TaskProcessResult(task_id=task_id, completed=True)
            await KnowledgeRepository.update_document_status(session, task["document_id"], "PARSING", error_message="")
            await KnowledgeRepository.update_task_status(session, task_id, "RUNNING")

        point_ids: list[str] = []
        collection_name = get_tenant_collection_name(task["tenant_id"])
        store = get_qdrant_store(collection_name)
        hybrid = get_hybrid_retriever(collection_name)
        try:
            storage_path = Path(task["storage_path"])
            if not storage_path.is_file():
                raise FileNotFoundError("原始文件不存在，无法执行入库任务")

            chunks = self._document_service.parse_and_split(str(storage_path), file_name=task["file_name"])
            if not chunks:
                raise ValueError("文档解析结果为空")

            await self._set_status(task["document_id"], task_id, "CHUNKING", "RUNNING", chunk_count=len(chunks))
            metadatas = [
                {
                    **chunk.metadata,
                    "document_id": task["document_id"],
                    "knowledge_base": "default",
                    "source": task["file_name"],
                }
                for chunk in chunks
            ]
            texts = [chunk.content for chunk in chunks]

            await self._set_status(task["document_id"], task_id, "INDEXING", "RUNNING", chunk_count=len(chunks))
            point_ids = store.add_documents(texts, metadatas)
            hybrid.add_documents(texts, metadatas)
            records = [
                {
                    "id": str(uuid4()),
                    "tenant_id": task["tenant_id"],
                    "document_id": task["document_id"],
                    "chunk_index": index,
                    "qdrant_point_id": point_ids[index],
                    "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "content_length": len(content),
                    "metadata": json.dumps(metadata, ensure_ascii=False),
                }
                for index, (content, metadata) in enumerate(zip(texts, metadatas, strict=True))
            ]
            async with session_scope() as session:
                await KnowledgeRepository.create_chunks(session, records)
                await KnowledgeRepository.update_document_status(
                    session, task["document_id"], "COMPLETED", chunk_count=len(chunks), error_message=""
                )
                await KnowledgeRepository.update_task_status(session, task_id, "COMPLETED")
                await KnowledgeRepository.write_audit_log(
                    session,
                    {
                        "tenant_id": task["tenant_id"],
                        "operator_id": task["created_by"],
                        "resource_type": "DOCUMENT",
                        "resource_id": task["document_id"],
                        "action": "INGEST_COMPLETED",
                        "detail": {"chunk_count": len(chunks), "task_id": task_id},
                        "trace_id": "",
                    },
                )
            from app.service.semantic_cache import get_semantic_cache

            get_semantic_cache().clear()
            logger.info("[Document] 异步入库完成 | document_id={} chunks={}", task["document_id"], len(chunks))
            return TaskProcessResult(task_id=task_id, completed=True, index_collection=collection_name)
        except Exception as exc:
            if point_ids:
                store.delete_points(point_ids)
                hybrid.remove_by_document_id(task["document_id"])
            async with session_scope() as session:
                retry_count = await KnowledgeRepository.mark_task_retry(session, task_id, str(exc))
                if retry_count <= settings.document_task_max_retries:
                    await KnowledgeRepository.update_document_status(
                        session, task["document_id"], "RETRYING", error_message=str(exc)[:1000]
                    )
                    logger.warning(
                        "[Document] 入库失败，准备重试 | task_id={} retry={}/{} error={}",
                        task_id,
                        retry_count,
                        settings.document_task_max_retries,
                        exc,
                    )
                    return TaskProcessResult(task_id=task_id, should_retry=True)
                await KnowledgeRepository.update_document_status(
                    session, task["document_id"], "FAILED", error_message=str(exc)[:1000]
                )
                await KnowledgeRepository.update_task_status(session, task_id, "FAILED", error_message=str(exc)[:1000])
            logger.exception("[Document] 入库任务最终失败 | task_id={} error={}", task_id, exc)
            return TaskProcessResult(task_id=task_id, completed=True)

    @staticmethod
    def _persist_source(tenant_id: str, document_id: str, suffix: str, content: bytes) -> Path:
        """持久化原文件，使 Worker 与后续重建索引拥有可靠输入。"""
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
        """以独立事务保存中间状态，保证 Worker 故障后状态可观测。"""
        async with session_scope() as session:
            await KnowledgeRepository.update_document_status(
                session, document_id, document_status, chunk_count=chunk_count
            )
            await KnowledgeRepository.update_task_status(session, task_id, task_status)
