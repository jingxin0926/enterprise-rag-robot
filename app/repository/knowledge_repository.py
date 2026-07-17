"""知识库文档元数据仓储。"""

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class KnowledgeRepository:
    """封装知识库文档、任务、切片和审计的数据库访问。"""

    @staticmethod
    async def ensure_default_knowledge_base(session: AsyncSession, tenant_id: str) -> int:
        """获取或创建租户默认知识库。"""
        result = await session.execute(
            text(
                """
                SELECT id FROM kb_knowledge_base
                WHERE tenant_id = :tenant_id AND code = 'default' AND deleted = 0
                """
            ),
            {"tenant_id": tenant_id},
        )
        knowledge_base_id = result.scalar_one_or_none()
        if knowledge_base_id:
            return int(knowledge_base_id)

        await session.execute(
            text(
                """
                INSERT INTO kb_knowledge_base(tenant_id, code, name, description)
                VALUES (:tenant_id, 'default', '默认知识库', '系统为租户自动创建的默认知识库')
                """
            ),
            {"tenant_id": tenant_id},
        )
        result = await session.execute(text("SELECT LAST_INSERT_ID()"))
        return int(result.scalar_one())

    @staticmethod
    async def create_document(session: AsyncSession, document: dict[str, Any]) -> None:
        """创建文档元数据记录。"""
        await session.execute(
            text(
                """
                INSERT INTO kb_document(
                    id, tenant_id, knowledge_base_id, file_name, file_extension, content_type,
                    file_size, checksum, storage_path, status, created_by
                ) VALUES (
                    :id, :tenant_id, :knowledge_base_id, :file_name, :file_extension, :content_type,
                    :file_size, :checksum, :storage_path, :status, :created_by
                )
                """
            ),
            document,
        )

    @staticmethod
    async def create_task(session: AsyncSession, task: dict[str, Any]) -> None:
        """创建入库任务记录。"""
        await session.execute(
            text(
                """
                INSERT INTO kb_ingest_task(id, tenant_id, document_id, task_type, status)
                VALUES (:id, :tenant_id, :document_id, :task_type, :status)
                """
            ),
            task,
        )

    @staticmethod
    async def ensure_legacy_document(session: AsyncSession, document: dict[str, Any]) -> None:
        """幂等创建历史回填文档，稳定 ID 保证重复执行不产生重复记录。"""
        await session.execute(
            text(
                """
                INSERT INTO kb_document(
                    id, tenant_id, knowledge_base_id, file_name, file_extension, content_type,
                    file_size, checksum, storage_path, status, chunk_count, created_by
                ) VALUES (
                    :id, :tenant_id, :knowledge_base_id, :file_name, :file_extension, :content_type,
                    :file_size, :checksum, :storage_path, :status, :chunk_count, :created_by
                ) ON DUPLICATE KEY UPDATE
                    file_name = VALUES(file_name),
                    file_size = VALUES(file_size),
                    chunk_count = VALUES(chunk_count),
                    status = VALUES(status),
                    error_message = ''
                """
            ),
            document,
        )

    @staticmethod
    async def ensure_legacy_task(session: AsyncSession, task: dict[str, Any]) -> None:
        """幂等记录历史回填任务结果。"""
        await session.execute(
            text(
                """
                INSERT INTO kb_ingest_task(id, tenant_id, document_id, task_type, status, finished_at)
                VALUES (:id, :tenant_id, :document_id, 'BACKFILL', 'COMPLETED', NOW())
                ON DUPLICATE KEY UPDATE status = 'COMPLETED', finished_at = NOW(), error_message = ''
                """
            ),
            task,
        )

    @staticmethod
    async def update_document_status(
        session: AsyncSession,
        document_id: str,
        status: str,
        *,
        chunk_count: int | None = None,
        error_message: str | None = None,
    ) -> None:
        """更新文档处理状态与可选统计信息。"""
        await session.execute(
            text(
                """
                UPDATE kb_document
                SET status = :status,
                    chunk_count = COALESCE(:chunk_count, chunk_count),
                    error_message = COALESCE(:error_message, error_message)
                WHERE id = :document_id AND deleted = 0
                """
            ),
            {
                "document_id": document_id,
                "status": status,
                "chunk_count": chunk_count,
                "error_message": error_message,
            },
        )

    @staticmethod
    async def update_task_status(
        session: AsyncSession,
        task_id: str,
        status: str,
        *,
        error_message: str = "",
    ) -> None:
        """更新任务状态与起止时间。"""
        now = datetime.now()
        await session.execute(
            text(
                """
                UPDATE kb_ingest_task
                SET status = :status,
                    error_message = :error_message,
                    started_at = CASE WHEN :status = 'RUNNING' THEN :now ELSE started_at END,
                    finished_at = CASE WHEN :status IN ('COMPLETED', 'FAILED') THEN :now ELSE finished_at END
                WHERE id = :task_id
                """
            ),
            {"task_id": task_id, "status": status, "error_message": error_message, "now": now},
        )

    @staticmethod
    async def get_task_context(session: AsyncSession, task_id: str) -> dict[str, Any] | None:
        """获取 Worker 处理文档任务所需的完整上下文。"""
        result = await session.execute(
            text(
                """
                SELECT t.id AS task_id, t.status AS task_status, t.retry_count,
                       d.id AS document_id, d.tenant_id, d.file_name, d.storage_path,
                       d.status AS document_status, d.created_by
                FROM kb_ingest_task t
                JOIN kb_document d ON d.id = t.document_id
                WHERE t.id = :task_id AND t.task_type = 'INGEST' AND d.deleted = 0
                """
            ),
            {"task_id": task_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    @staticmethod
    async def get_task_detail(session: AsyncSession, tenant_id: str, task_id: str) -> dict[str, Any] | None:
        """查询租户可见的任务状态，供前端轮询。"""
        result = await session.execute(
            text(
                """
                SELECT t.id, t.document_id, t.task_type, t.status, t.retry_count, t.error_message,
                       t.started_at, t.finished_at, t.create_time, t.update_time,
                       d.file_name, d.chunk_count, d.status AS document_status
                FROM kb_ingest_task t
                JOIN kb_document d ON d.id = t.document_id
                WHERE t.id = :task_id AND t.tenant_id = :tenant_id
                """
            ),
            {"tenant_id": tenant_id, "task_id": task_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    @staticmethod
    async def mark_task_retry(session: AsyncSession, task_id: str, error_message: str) -> int:
        """递增失败次数并返回递增后的次数。"""
        await session.execute(
            text(
                """
                UPDATE kb_ingest_task
                SET status = 'RETRYING', retry_count = retry_count + 1, error_message = :error_message
                WHERE id = :task_id
                """
            ),
            {"task_id": task_id, "error_message": error_message[:1000]},
        )
        result = await session.execute(
            text("SELECT retry_count FROM kb_ingest_task WHERE id = :task_id"), {"task_id": task_id}
        )
        return int(result.scalar_one())

    @staticmethod
    async def create_chunks(session: AsyncSession, chunks: Sequence[dict[str, Any]]) -> None:
        """批量保存切片元数据。"""
        if not chunks:
            return
        await session.execute(
            text(
                """
                INSERT INTO kb_document_chunk(
                    id, tenant_id, document_id, chunk_index, qdrant_point_id,
                    content_hash, content_length, metadata
                ) VALUES (
                    :id, :tenant_id, :document_id, :chunk_index, :qdrant_point_id,
                    :content_hash, :content_length, :metadata
                )
                """
            ),
            list(chunks),
        )

    @staticmethod
    async def ensure_legacy_chunks(session: AsyncSession, chunks: Sequence[dict[str, Any]]) -> None:
        """幂等写入历史向量的切片元数据。"""
        if not chunks:
            return
        await session.execute(
            text(
                """
                INSERT INTO kb_document_chunk(
                    id, tenant_id, document_id, chunk_index, qdrant_point_id,
                    content_hash, content_length, metadata
                ) VALUES (
                    :id, :tenant_id, :document_id, :chunk_index, :qdrant_point_id,
                    :content_hash, :content_length, :metadata
                ) ON DUPLICATE KEY UPDATE
                    metadata = VALUES(metadata),
                    content_length = VALUES(content_length),
                    deleted = 0
                """
            ),
            list(chunks),
        )

    @staticmethod
    async def write_audit_log(session: AsyncSession, audit: dict[str, Any]) -> None:
        """写入不可变操作审计记录。"""
        await session.execute(
            text(
                """
                INSERT INTO sys_operation_log(tenant_id, operator_id, resource_type, resource_id, action, detail, trace_id)
                VALUES (:tenant_id, :operator_id, :resource_type, :resource_id, :action, :detail, :trace_id)
                """
            ),
            {**audit, "detail": json.dumps(audit.get("detail", {}), ensure_ascii=False)},
        )

    @staticmethod
    async def list_documents(
        session: AsyncSession,
        tenant_id: str,
        page: int,
        page_size: int,
    ) -> tuple[int, list[dict[str, Any]]]:
        """分页查询租户文档。"""
        total = await session.execute(
            text("SELECT COUNT(*) FROM kb_document WHERE tenant_id = :tenant_id AND deleted = 0"),
            {"tenant_id": tenant_id},
        )
        items = await session.execute(
            text(
                """
                SELECT id, knowledge_base_id, file_name, file_extension, file_size, status,
                       chunk_count, error_message, version_no, create_time, update_time
                FROM kb_document
                WHERE tenant_id = :tenant_id AND deleted = 0
                ORDER BY create_time DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"tenant_id": tenant_id, "limit": page_size, "offset": (page - 1) * page_size},
        )
        return int(total.scalar_one()), [dict(row) for row in items.mappings().all()]

    @staticmethod
    async def get_document(session: AsyncSession, tenant_id: str, document_id: str) -> dict[str, Any] | None:
        """查询租户内单个有效文档。"""
        result = await session.execute(
            text(
                """
                SELECT id, tenant_id, file_name, storage_path, status, chunk_count
                FROM kb_document
                WHERE id = :document_id AND tenant_id = :tenant_id AND deleted = 0
                """
            ),
            {"tenant_id": tenant_id, "document_id": document_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    @staticmethod
    async def mark_document_deleted(session: AsyncSession, document_id: str) -> None:
        """逻辑删除文档和其切片元数据。"""
        await session.execute(
            text("UPDATE kb_document SET status = 'DELETED', deleted = 1 WHERE id = :document_id"),
            {"document_id": document_id},
        )
        await session.execute(
            text("UPDATE kb_document_chunk SET deleted = 1 WHERE document_id = :document_id"),
            {"document_id": document_id},
        )
