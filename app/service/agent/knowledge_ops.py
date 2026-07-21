"""受控的知识运营 Agent。

该模块面向知识库管理员的日常排障与运营查询。与通用 Agent 不同，
它只暴露白名单内的只读工具，工具参数通过 Pydantic 校验，并且每次
调用都写入 ``sys_operation_log``，使模型行为可以被回放与审计。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError, model_validator

from app.core.config import settings
from app.infra.database.database import session_scope
from app.prompts.loader import get_prompt_loader
from app.repository.knowledge_repository import KnowledgeRepository
from app.service.evaluation_history_service import EvaluationHistoryService
from app.service.retrieval.hybrid_retriever import get_hybrid_retriever

MAX_TOOL_ROUNDS = 3
MAX_TOOL_RESULT_CHARS = 6_000


class SearchKnowledgeInput(BaseModel):
    """知识检索工具的输入契约。"""

    query: str = Field(min_length=1, max_length=500, description="待检索的知识库问题或关键词")
    top_k: int = Field(default=3, ge=1, le=5, description="返回的最多片段数")


class DocumentStatusInput(BaseModel):
    """文档状态查询工具的输入契约。"""

    document_id: str | None = Field(default=None, min_length=1, max_length=36, description="文档 ID")
    file_name_keyword: str | None = Field(default=None, min_length=1, max_length=120, description="文档文件名关键词")
    limit: int = Field(default=5, ge=1, le=10, description="最多返回文档数")

    @model_validator(mode="after")
    def require_lookup_condition(self) -> DocumentStatusInput:
        """避免无条件扫描整个租户的文档目录。"""
        if not self.document_id and not self.file_name_keyword:
            raise ValueError("document_id 和 file_name_keyword 至少提供一个")
        return self


class EvaluationSummaryInput(BaseModel):
    """评测摘要查询工具的输入契约。"""

    limit: int = Field(default=3, ge=1, le=10, description="返回最近的评测运行数")


@dataclass
class KnowledgeOpsResponse:
    """知识运营 Agent 的最终响应。"""

    answer: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    total_tokens: int = 0
    rounds: int = 0


class KnowledgeOpsAgent:
    """以只读、可审计工具执行知识库运营查询。"""

    _tool_inputs: dict[str, type[BaseModel]] = {
        "search_knowledge": SearchKnowledgeInput,
        "get_document_status": DocumentStatusInput,
        "get_evaluation_summary": EvaluationSummaryInput,
    }

    def __init__(self, tenant_id: str, operator_id: str, trace_id: str) -> None:
        self._tenant_id = tenant_id
        self._operator_id = operator_id
        self._trace_id = trace_id

    @classmethod
    def tool_schemas(cls) -> list[dict[str, Any]]:
        """返回显式定义的 OpenAI function-calling 工具契约。"""
        descriptions = {
            "search_knowledge": "在当前租户知识库执行混合检索，返回带来源和原始检索分数的证据片段。",
            "get_document_status": "按文档 ID 或文件名关键词查询当前租户的入库状态、任务重试次数和失败原因。",
            "get_evaluation_summary": "查询当前租户最近的 RAG 回归评测结果及与可比基线的趋势。",
        }
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": descriptions[name],
                    "parameters": input_model.model_json_schema(),
                },
            }
            for name, input_model in cls._tool_inputs.items()
        ]

    async def run(self, message: str) -> KnowledgeOpsResponse:
        """执行受控 Agent 循环，最大三轮工具调用后强制输出结论。"""
        client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            timeout=settings.deepseek_timeout,
        )
        system_prompt = get_prompt_loader().load("knowledge_ops_agent", tenant_id=self._tenant_id)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message},
        ]
        tool_calls: list[dict[str, Any]] = []
        total_tokens = 0

        for round_index in range(MAX_TOOL_ROUNDS + 1):
            response = await client.chat.completions.create(
                model=settings.deepseek_model,
                messages=messages,
                tools=self.tool_schemas() if round_index < MAX_TOOL_ROUNDS else None,
                temperature=0.1,
                max_tokens=settings.deepseek_max_tokens,
            )
            if response.usage:
                total_tokens += response.usage.total_tokens

            assistant_message = response.choices[0].message
            if not assistant_message.tool_calls:
                return KnowledgeOpsResponse(
                    answer=assistant_message.content or "未生成可用结论。",
                    tool_calls=tool_calls,
                    total_tokens=total_tokens,
                    rounds=round_index + 1,
                )

            messages.append(assistant_message.model_dump())
            for tool_call in assistant_message.tool_calls:
                result, audit_record = await self.execute_tool_call(
                    tool_call.function.name,
                    tool_call.function.arguments,
                )
                tool_calls.append(audit_record)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    }
                )

        return KnowledgeOpsResponse(
            answer="工具调用轮次已达上限，请缩小查询范围后重试。",
            tool_calls=tool_calls,
            total_tokens=total_tokens,
            rounds=MAX_TOOL_ROUNDS + 1,
        )

    async def execute_tool_call(self, tool_name: str, raw_arguments: str) -> tuple[str, dict[str, Any]]:
        """校验、执行并审计一次白名单工具调用。"""
        started_at = time.perf_counter()
        success = False
        result_payload: dict[str, Any]
        validated_args: BaseModel | None = None

        try:
            input_model = self._tool_inputs.get(tool_name)
            if input_model is None:
                raise ValueError(f"不允许调用工具: {tool_name}")
            arguments = json.loads(raw_arguments) if raw_arguments else {}
            validated_args = input_model.model_validate(arguments)
            result_payload = await self._invoke_tool(tool_name, validated_args)
            success = True
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            result_payload = {"ok": False, "error": f"工具参数不合法: {exc}"}
        except Exception:  # 工具失败也必须审计，便于排障。
            logger.exception("[KnowledgeOpsAgent] 工具执行失败 | tool={}", tool_name)
            result_payload = {"ok": False, "error": "工具执行失败，请稍后重试"}

        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        audit_record = {
            "tool": tool_name,
            "success": success,
            "duration_ms": duration_ms,
            "args": self._summarize_args(validated_args),
            "result_count": len(result_payload.get("items", [])),
        }
        await self._write_audit(audit_record)
        return self._serialize_tool_result(result_payload), audit_record

    async def _invoke_tool(self, tool_name: str, args: BaseModel) -> dict[str, Any]:
        """按白名单分发工具；禁止模型传入租户与操作人。"""
        if tool_name == "search_knowledge":
            return await self._search_knowledge(args)
        if tool_name == "get_document_status":
            return await self._get_document_status(args)
        if tool_name == "get_evaluation_summary":
            return await self._get_evaluation_summary(args)
        raise ValueError(f"不允许调用工具: {tool_name}")

    async def _search_knowledge(self, args: BaseModel) -> dict[str, Any]:
        """查询当前租户的混合检索器并保留来源证据。"""
        request = SearchKnowledgeInput.model_validate(args)
        results = get_hybrid_retriever().search(query=request.query, top_k=request.top_k)
        items = [
            {
                "source": result.metadata.get("source", "未知来源"),
                "document_id": result.metadata.get("document_id", ""),
                "chunk_index": result.metadata.get("chunk_index"),
                "content": result.content[:1_500],
                "vector_score": result.vector_score,
                "bm25_score": result.bm25_score,
                "rrf_score": result.rrf_score,
            }
            for result in results
        ]
        return {"ok": True, "items": items, "message": "检索完成" if items else "未检索到相关片段"}

    async def _get_document_status(self, args: BaseModel) -> dict[str, Any]:
        """只读取当前租户的文档和最新入库任务状态。"""
        request = DocumentStatusInput.model_validate(args)
        async with session_scope() as session:
            items = await KnowledgeRepository.find_documents_for_ops(
                session=session,
                tenant_id=self._tenant_id,
                document_id=request.document_id,
                file_name_keyword=request.file_name_keyword,
                limit=request.limit,
            )
        return {"ok": True, "items": items, "message": "查询完成" if items else "未找到匹配文档"}

    async def _get_evaluation_summary(self, args: BaseModel) -> dict[str, Any]:
        """读取当前租户的评测运行历史，不触发新的模型评测。"""
        request = EvaluationSummaryInput.model_validate(args)
        runs = await EvaluationHistoryService().list_runs(self._tenant_id, request.limit)
        items = [
            {
                "run_id": run["id"],
                "status": run["status"],
                "git_commit": run.get("git_commit"),
                "source_recall": run.get("source_recall"),
                "answer_point_coverage": run.get("answer_point_coverage"),
                "refusal_accuracy": run.get("refusal_accuracy"),
                "average_latency_ms": run.get("average_latency_ms"),
                "comparison": run.get("comparison"),
                "finished_at": run.get("finished_at"),
            }
            for run in runs
        ]
        return {"ok": True, "items": items, "message": "查询完成" if items else "暂无评测运行记录"}

    async def _write_audit(self, record: dict[str, Any]) -> None:
        """写入工具调用审计；审计失败时拒绝返回工具数据。"""
        try:
            async with session_scope() as session:
                await KnowledgeRepository.write_audit_log(
                    session,
                    {
                        "tenant_id": self._tenant_id,
                        "operator_id": self._operator_id,
                        "resource_type": "AGENT_TOOL",
                        "resource_id": record["tool"],
                        "action": "READ",
                        "detail": record,
                        "trace_id": self._trace_id,
                    },
                )
        except Exception as exc:
            logger.exception("[KnowledgeOpsAgent] 审计写入失败 | tool={}", record["tool"])
            raise RuntimeError("审计记录失败，已阻止返回工具数据") from exc

    @staticmethod
    def _summarize_args(args: BaseModel | None) -> dict[str, Any]:
        """记录可排障但不直接保存用户完整查询内容的参数摘要。"""
        if args is None:
            return {"valid": False}
        payload = args.model_dump(exclude_none=True)
        summary: dict[str, Any] = {"valid": True, "keys": sorted(payload)}
        if "query" in payload:
            query = str(payload["query"])
            summary["query_length"] = len(query)
            summary["query_sha256"] = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
        if "document_id" in payload:
            summary["document_id"] = payload["document_id"]
        if "file_name_keyword" in payload:
            summary["file_name_keyword_length"] = len(str(payload["file_name_keyword"]))
        if "top_k" in payload:
            summary["top_k"] = payload["top_k"]
        if "limit" in payload:
            summary["limit"] = payload["limit"]
        return summary

    @staticmethod
    def _serialize_tool_result(payload: dict[str, Any]) -> str:
        """限制反馈给模型的工具结果大小，防止上下文被大文档耗尽。"""
        result = json.dumps(payload, ensure_ascii=False, default=str)
        return result[:MAX_TOOL_RESULT_CHARS]
