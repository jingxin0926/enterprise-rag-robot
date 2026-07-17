"""文档列表与删除业务服务。"""

from pathlib import Path
from typing import Any

from app.infra.database.database import session_scope
from app.infra.vector.qdrant_store import get_qdrant_store
from app.repository.knowledge_repository import KnowledgeRepository
from app.service.retrieval.hybrid_retriever import get_hybrid_retriever


class DocumentManagementService:
    """管理已入库文档的查询和删除。"""

    async def list_documents(self, tenant_id: str, page: int, page_size: int) -> tuple[int, list[dict[str, Any]]]:
        """分页查询当前租户的文档元数据。"""
        async with session_scope() as session:
            return await KnowledgeRepository.list_documents(session, tenant_id, page, page_size)

    async def delete_document(
        self,
        *,
        tenant_id: str,
        operator_id: str,
        document_id: str,
        trace_id: str,
    ) -> bool:
        """删除元数据、向量、BM25 索引和原始文件。"""
        async with session_scope() as session:
            document = await KnowledgeRepository.get_document(session, tenant_id, document_id)
            if document is None:
                return False
            await KnowledgeRepository.update_document_status(session, document_id, "DELETING")

        get_qdrant_store().delete_by_document_id(document_id)
        get_hybrid_retriever().remove_by_document_id(document_id)

        async with session_scope() as session:
            await KnowledgeRepository.mark_document_deleted(session, document_id)
            await KnowledgeRepository.write_audit_log(
                session,
                {
                    "tenant_id": tenant_id,
                    "operator_id": operator_id,
                    "resource_type": "DOCUMENT",
                    "resource_id": document_id,
                    "action": "DELETE",
                    "detail": {"file_name": document["file_name"]},
                    "trace_id": trace_id,
                },
            )

        if document["storage_path"]:
            storage_path = Path(document["storage_path"])
            if storage_path.is_file():
                storage_path.unlink(missing_ok=True)
        from app.service.semantic_cache import get_semantic_cache

        get_semantic_cache().clear()
        return True
