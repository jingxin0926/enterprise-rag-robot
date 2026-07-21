"""KnowledgeOpsAgent 的权限边界与工具审计测试。"""

import json
from unittest.mock import AsyncMock

import pytest

from app.service.agent.knowledge_ops import DocumentStatusInput, KnowledgeOpsAgent, SearchKnowledgeInput


def test_document_status_requires_a_lookup_condition() -> None:
    """禁止 Agent 无条件扫描一个租户的所有文档。"""
    with pytest.raises(ValueError, match="至少提供一个"):
        DocumentStatusInput()


def test_knowledge_ops_exposes_only_typed_read_tools() -> None:
    """工具 schema 必须来自 Pydantic 契约，且不包含任何写操作。"""
    schemas = KnowledgeOpsAgent.tool_schemas()
    names = {schema["function"]["name"] for schema in schemas}

    assert names == {"search_knowledge", "get_document_status", "get_evaluation_summary"}
    search_schema = next(schema for schema in schemas if schema["function"]["name"] == "search_knowledge")
    assert search_schema["function"]["parameters"]["properties"]["top_k"]["maximum"] == 5
    assert "delete_document" not in names
    assert "retry_task" not in names


@pytest.mark.asyncio
async def test_tool_call_validates_then_writes_auditable_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    """工具调用只记录查询摘要，不把原问题全文写入审计表。"""
    agent = KnowledgeOpsAgent(tenant_id="tenant-a", operator_id="admin-a", trace_id="trace-a")
    agent._search_knowledge = AsyncMock(return_value={"ok": True, "items": [{"source": "手册.md"}]})
    write_audit = AsyncMock()

    class FakeSessionScope:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

    monkeypatch.setattr("app.service.agent.knowledge_ops.session_scope", lambda: FakeSessionScope())
    monkeypatch.setattr("app.service.agent.knowledge_ops.KnowledgeRepository.write_audit_log", write_audit)

    result, record = await agent.execute_tool_call(
        "search_knowledge",
        json.dumps({"query": "图片作品改造的审批流程", "top_k": 2}),
    )

    assert json.loads(result)["ok"] is True
    assert record["success"] is True
    assert record["result_count"] == 1
    assert record["args"]["query_length"] > 0
    assert "query" not in record["args"]
    write_audit.assert_awaited_once()
    persisted = write_audit.await_args.args[1]
    assert persisted["tenant_id"] == "tenant-a"
    assert persisted["action"] == "READ"
    assert persisted["trace_id"] == "trace-a"


@pytest.mark.asyncio
async def test_tool_call_rejects_unknown_tool_and_keeps_audit_record(monkeypatch: pytest.MonkeyPatch) -> None:
    """模型即使生成未知函数名，也不能越过白名单。"""
    agent = KnowledgeOpsAgent(tenant_id="tenant-a", operator_id="admin-a", trace_id="trace-a")
    agent._write_audit = AsyncMock()

    result, record = await agent.execute_tool_call("delete_document", "{}")

    assert json.loads(result)["ok"] is False
    assert record["success"] is False
    assert record["tool"] == "delete_document"
    agent._write_audit.assert_awaited_once()


def test_search_knowledge_input_bounds_result_size() -> None:
    """模型不能把检索 Top-K 放大为无界上下文。"""
    assert SearchKnowledgeInput(query="审批流", top_k=5).top_k == 5
    with pytest.raises(ValueError):
        SearchKnowledgeInput(query="审批流", top_k=6)
