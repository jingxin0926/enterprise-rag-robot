"""P1 知识库元数据基础行为测试。"""

from unittest.mock import AsyncMock

import pytest

from app.core.config import PROJECT_ROOT
from app.infra.queue.document_task_queue import DocumentTaskQueue
from app.service.knowledge.legacy_backfill_service import LegacyBackfillService
from app.service.retrieval.bm25_retriever import BM25Retriever


def test_document_delete_removes_bm25_chunks() -> None:
    """删除文档后，关键词检索不应再召回其切片。"""
    retriever = BM25Retriever()
    retriever.add_documents(
        ["OpenIM 用户注册接口", "订单退款流程"],
        [{"document_id": "doc-openim"}, {"document_id": "doc-order"}],
    )

    assert retriever.remove_by_document_id("doc-openim") == 1
    results = retriever.search("OpenIM 用户注册")

    assert all(result.metadata["document_id"] != "doc-openim" for result in results)
    assert retriever.doc_count == 1


def test_initial_migration_contains_document_lifecycle_tables() -> None:
    """初始迁移必须包含 P1 文档生命周期涉及的所有表。"""
    migration = (PROJECT_ROOT / "db" / "migrations" / "V001__knowledge_metadata.sql").read_text(encoding="utf-8")

    for table in (
        "kb_knowledge_base",
        "kb_document",
        "kb_ingest_task",
        "kb_document_chunk",
        "sys_operation_log",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in migration

    assert "qdrant_point_id VARCHAR(64) NOT NULL" in migration


def test_follow_up_migration_expands_legacy_qdrant_point_id() -> None:
    """历史 UUID 点 ID 带连字符，迁移必须兼容至少 36 位。"""
    migration = (PROJECT_ROOT / "db" / "migrations" / "V002__expand_qdrant_point_id.sql").read_text(encoding="utf-8")

    assert "qdrant_point_id VARCHAR(64) NOT NULL" in migration


def test_legacy_points_are_grouped_by_source_with_fallback_name() -> None:
    """历史回填按来源聚合，缺失来源时归入统一的可识别分组。"""
    grouped = LegacyBackfillService.group_by_source(
        [
            {"point_id": "p1", "payload": {"source": "研发规范.md", "content": "A"}},
            {"point_id": "p2", "payload": {"source": "研发规范.md", "content": "B"}},
            {"point_id": "p3", "payload": {"content": "C"}},
        ]
    )

    assert len(grouped["研发规范.md"]) == 2
    assert grouped["历史未命名文档"][0]["point_id"] == "p3"


@pytest.mark.asyncio
async def test_document_task_queue_enqueues_task_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """上传接口投递的消息只携带任务 ID，业务数据以 MySQL 为准。"""
    redis = AsyncMock()
    monkeypatch.setattr("app.infra.queue.document_task_queue.get_redis", AsyncMock(return_value=redis))

    await DocumentTaskQueue().enqueue("task-001")

    redis.lpush.assert_awaited_once_with("smartqa:document-task:pending", '{"task_id": "task-001"}')
