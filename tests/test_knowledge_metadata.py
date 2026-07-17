"""P1 知识库元数据基础行为测试。"""

from app.core.config import PROJECT_ROOT
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
