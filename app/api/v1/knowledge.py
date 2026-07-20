"""
知识库管理接口

功能：
1. POST /api/v1/knowledge/upload   — 上传文档到知识库
2. POST /api/v1/knowledge/query    — 知识库问答（RAG）
3. GET  /api/v1/knowledge/info     — 获取知识库信息
"""

from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, UploadFile
from loguru import logger
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.api.deps import get_current_user
from app.core.response import R
from app.core.security import TokenPayload
from app.infra.database.database import session_scope
from app.infra.queue.document_task_queue import DocumentTaskQueue
from app.infra.vector.qdrant_store import get_qdrant_store
from app.middleware.trace import get_trace_id
from app.repository.knowledge_repository import KnowledgeRepository
from app.service.knowledge.document_ingest_service import DocumentIngestService
from app.service.knowledge.document_management_service import DocumentManagementService
from app.service.knowledge.legacy_backfill_service import LegacyBackfillService
from app.service.rag_service import RAGService

router = APIRouter(prefix="/knowledge", tags=["知识库"])

# 服务实例
_document_ingest_service = DocumentIngestService()
_document_management_service = DocumentManagementService()
_legacy_backfill_service = LegacyBackfillService()
_document_task_queue = DocumentTaskQueue()


class KnowledgeQueryRequest(BaseModel):
    """知识库问答请求"""

    question: str = Field(..., min_length=1, max_length=2000, description="问题")
    stream: bool = Field(default=False, description="是否流式输出")
    top_k: int = Field(default=5, ge=1, le=20, description="检索 Top-K")


class TextUploadRequest(BaseModel):
    """文本直接上传请求"""

    content: str = Field(..., min_length=10, description="文本内容")
    source_name: str = Field(default="粘贴文本", description="来源名称")


class DocumentPageResponse(BaseModel):
    """文档分页响应。"""

    total: int
    page: int
    page_size: int
    items: list[dict]


# 支持的文件类型
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".md", ".markdown", ".txt"}
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB


@router.post("/upload", summary="上传文档到知识库")
async def upload_document(file: UploadFile = File(...), user: TokenPayload = Depends(get_current_user)):
    """
    上传文件到知识库

    支持格式：PDF / DOCX / MD / TXT（最大 20MB）
    流程：上传 → 解析 → 切分 → 向量化 → 存入 Qdrant
    """
    # 1. 校验文件类型
    filename = file.filename or "unknown"
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        return R.fail(
            code=400,
            message=f"不支持的文件格式: {suffix}（支持: {', '.join(ALLOWED_EXTENSIONS)}）",
            trace_id=get_trace_id(),
        )

    # 2. 读取文件内容
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        return R.fail(code=400, message="文件大小超过 20MB 限制", trace_id=get_trace_id())

    try:
        result = await _document_ingest_service.submit(
            tenant_id=user.tenant_id,
            operator_id=user.user_id,
            file_name=filename,
            content=content,
            content_type=file.content_type or "",
            trace_id=get_trace_id(),
        )
        await _document_task_queue.enqueue(result.task_id)

        return R.success(
            data={
                "document_id": result.document_id,
                "task_id": result.task_id,
                "filename": result.file_name,
                "status": "PENDING",
            },
            message=f"文档 '{filename}' 已提交入库任务",
            trace_id=get_trace_id(),
        )
    except Exception as exc:
        logger.exception("[Knowledge] 文档入库失败 | file={} error={}", filename, exc)
        return R.fail(code=500, message="文档入库失败，请查看文档处理状态或服务日志", trace_id=get_trace_id())


@router.post("/upload_text", summary="直接上传文本到知识库")
async def upload_text(req: TextUploadRequest, user: TokenPayload = Depends(get_current_user)):
    """
    直接粘贴文本到知识库（不需要文件）

    适合小段文本、FAQ 等场景
    """
    source_name = req.source_name if Path(req.source_name).suffix else f"{req.source_name}.txt"
    result = await _document_ingest_service.submit(
        tenant_id=user.tenant_id,
        operator_id=user.user_id,
        file_name=source_name,
        content=req.content.encode("utf-8"),
        content_type="text/plain; charset=utf-8",
        trace_id=get_trace_id(),
    )
    await _document_task_queue.enqueue(result.task_id)

    return R.success(
        data={
            "document_id": result.document_id,
            "task_id": result.task_id,
            "source": result.file_name,
            "status": "PENDING",
        },
        message=f"文本 '{result.file_name}' 已提交入库任务",
        trace_id=get_trace_id(),
    )


@router.get("/documents", summary="分页查询文档")
async def list_documents(
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=20, ge=1, le=100, description="每页条数"),
    user: TokenPayload = Depends(get_current_user),
):
    """查询当前租户的文档处理状态与入库统计。"""
    total, items = await _document_management_service.list_documents(user.tenant_id, page, page_size)
    return R.success(
        data=DocumentPageResponse(total=total, page=page, page_size=page_size, items=items).model_dump(mode="json"),
        trace_id=get_trace_id(),
    )


@router.get("/tasks/{task_id}", summary="查询入库任务")
async def get_ingest_task(task_id: str, user: TokenPayload = Depends(get_current_user)):
    """查询当前租户的异步入库任务状态、重试次数和失败原因。"""
    async with session_scope() as session:
        task = await KnowledgeRepository.get_task_detail(session, user.tenant_id, task_id)
    if task is None:
        return R.fail(code=404, message="入库任务不存在", trace_id=get_trace_id())
    return R.success(data=task, trace_id=get_trace_id())


@router.delete("/documents/{document_id}", summary="删除文档")
async def delete_document(document_id: str, user: TokenPayload = Depends(get_current_user)):
    """删除文档原件、元数据、向量切片与 BM25 索引。"""
    deleted = await _document_management_service.delete_document(
        tenant_id=user.tenant_id,
        operator_id=user.user_id,
        document_id=document_id,
        trace_id=get_trace_id(),
    )
    if not deleted:
        return R.fail(code=404, message="文档不存在或已删除", trace_id=get_trace_id())
    return R.success(data={"document_id": document_id}, message="文档已删除", trace_id=get_trace_id())


@router.post("/backfill-legacy", summary="回填历史向量元数据")
async def backfill_legacy_documents(user: TokenPayload = Depends(get_current_user)):
    """将当前租户未关联文档的历史向量转换为可管理文档，仅管理员可执行。"""
    if user.role != "admin":
        return R.fail(code=403, message="仅管理员可执行历史数据回填", trace_id=get_trace_id())

    result = await _legacy_backfill_service.backfill(
        tenant_id=user.tenant_id,
        operator_id=user.user_id,
        trace_id=get_trace_id(),
    )
    return R.success(
        data={
            "documents_created": result.documents_created,
            "chunks_backfilled": result.chunks_backfilled,
            "skipped_chunks": result.skipped_chunks,
        },
        message=f"历史数据回填完成，新增 {result.documents_created} 个文档、{result.chunks_backfilled} 个切片",
        trace_id=get_trace_id(),
    )


@router.post("/query", summary="知识库问答 (RAG)")
async def query_knowledge(req: KnowledgeQueryRequest, user: TokenPayload = Depends(get_current_user)):
    """
    基于知识库的智能问答

    流程：问题 → 向量检索 → 上下文拼接 → LLM 生成
    支持流式和非流式输出
    """
    rag_service = RAGService(top_k=req.top_k)

    # 流式输出
    if req.stream:

        async def event_generator():
            try:
                async for chunk in rag_service.query_stream(req.question):
                    yield {"event": "message", "data": chunk}
                yield {"event": "done", "data": "{}"}
            except Exception as e:
                logger.exception("[RAG SSE] 异常: {}", e)
                yield {"event": "error", "data": str(e)}

        return EventSourceResponse(event_generator())

    # 非流式输出
    result = await rag_service.query(req.question)
    return R.success(
        data={
            "answer": result.answer,
            "sources": result.sources,
            "rewritten_query": result.rewritten_query,
            "retrieval_mode": result.retrieval_mode,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.total_tokens,
            "answer_status": result.answer_status,
            "evidence_count": result.evidence_count,
            "evidence_verdict": result.evidence_verdict,
        },
        trace_id=get_trace_id(),
    )


@router.get("/info", summary="知识库信息")
async def knowledge_info(user: TokenPayload = Depends(get_current_user)):
    """获取知识库基本信息（文档数量等）"""
    store = get_qdrant_store()
    info = store.get_collection_info()
    return R.success(data=info, trace_id=get_trace_id())
