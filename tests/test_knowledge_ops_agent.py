"""KnowledgeOpsAgent 的权限边界与工具审计测试。"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.v1.agent import KnowledgeOpsRequest, knowledge_ops_agent
from app.core.security import TokenPayload
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


@pytest.mark.asyncio
async def test_knowledge_ops_rejects_non_admin_before_invoking_agent() -> None:
    """知识运营能力仅对管理员开放，普通用户不能进入模型调用链路。"""
    user = TokenPayload(user_id="user-a", username="operator", tenant_id="tenant-a", role="user")
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/agent/knowledge-ops",
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_ops_agent(
            request=request,
            req=KnowledgeOpsRequest(message="check document status"),
            user=user,
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_document_status_query_is_scoped_to_agent_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    """工具必须把 Agent 的租户 ID 传给仓储层，不能信任模型输入。"""
    agent = KnowledgeOpsAgent(tenant_id="tenant-a", operator_id="admin-a", trace_id="trace-a")
    find_documents = AsyncMock(return_value=[])

    class FakeSessionScope:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            return None

    monkeypatch.setattr("app.service.agent.knowledge_ops.session_scope", lambda: FakeSessionScope())
    monkeypatch.setattr("app.service.agent.knowledge_ops.KnowledgeRepository.find_documents_for_ops", find_documents)

    await agent._get_document_status(DocumentStatusInput(document_id="document-a"))

    assert find_documents.await_args.kwargs["tenant_id"] == "tenant-a"


@pytest.mark.asyncio
async def test_search_uses_the_agent_tenant_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    """即使上下文变量异常，检索也必须使用经过认证的 Agent 租户。"""
    agent = KnowledgeOpsAgent(tenant_id="tenant-a", operator_id="admin-a", trace_id="trace-a")

    class FakeRetriever:
        def search(self, query: str, top_k: int) -> list[object]:
            assert query == "approval flow"
            assert top_k == 2
            return []

    get_retriever = MagicMock(return_value=FakeRetriever())
    monkeypatch.setattr("app.service.agent.knowledge_ops.get_hybrid_retriever", get_retriever)

    result = await agent._search_knowledge(SearchKnowledgeInput(query="approval flow", top_k=2))

    assert result["items"] == []
    assert get_retriever.call_args.kwargs["collection_name"] == "tenant_tenant-a_knowledge"


@pytest.mark.asyncio
async def test_tool_timeout_is_audited_and_never_returns_tool_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """慢工具也要形成审计记录，并向模型返回确定性失败信息。"""
    agent = KnowledgeOpsAgent(tenant_id="tenant-a", operator_id="admin-a", trace_id="trace-a")
    agent._write_audit = AsyncMock()
    monkeypatch.setattr("app.service.agent.knowledge_ops.settings.agent_tool_timeout_seconds", 0.001)

    async def timeout_tool(*args: object, **kwargs: object) -> dict[str, object]:
        await asyncio.Event().wait()
        return {"ok": True}

    agent._invoke_tool = timeout_tool  # type: ignore[method-assign]

    result, record = await agent.execute_tool_call("search_knowledge", json.dumps({"query": "approval flow"}))

    assert json.loads(result) == {"ok": False, "error": "工具调用超时，请缩小查询范围后重试"}
    assert record["success"] is False
    agent._write_audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_audit_failure_blocks_tool_result() -> None:
    """审计写入失败时不能把任何工具结果泄露给模型。"""
    agent = KnowledgeOpsAgent(tenant_id="tenant-a", operator_id="admin-a", trace_id="trace-a")
    agent._search_knowledge = AsyncMock(return_value={"ok": True, "items": [{"source": "manual.md"}]})
    agent._write_audit = AsyncMock(side_effect=RuntimeError("audit unavailable"))

    with pytest.raises(RuntimeError, match="audit unavailable"):
        await agent.execute_tool_call("search_knowledge", json.dumps({"query": "approval flow"}))


def test_search_knowledge_input_bounds_result_size() -> None:
    """模型不能把检索 Top-K 放大为无界上下文。"""
    assert SearchKnowledgeInput(query="审批流", top_k=5).top_k == 5
    with pytest.raises(ValueError):
        SearchKnowledgeInput(query="审批流", top_k=6)
