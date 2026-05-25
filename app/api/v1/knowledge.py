"""
知识库管理接口

功能：
1. POST /api/v1/knowledge/upload   — 上传文档到知识库
2. POST /api/v1/knowledge/query    — 知识库问答（RAG）
3. GET  /api/v1/knowledge/info     — 获取知识库信息
"""

import tempfile
from pathlib import Path

from fastapi import APIRouter, File, UploadFile
from loguru import logger
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.core.response import R
from app.middleware.trace import get_trace_id
from app.service.document_service import DocumentService
from app.infra.vector.qdrant_store import get_qdrant_store
from app.service.rag_service import RAGService
from app.service.retrieval.hybrid_retriever import get_hybrid_retriever

router = APIRouter(prefix="/knowledge", tags=["知识库"])

# 服务实例
_doc_service = DocumentService()


class KnowledgeQueryRequest(BaseModel):
    """知识库问答请求"""

    question: str = Field(..., min_length=1, max_length=2000, description="问题")
    stream: bool = Field(default=False, description="是否流式输出")
    top_k: int = Field(default=5, ge=1, le=20, description="检索 Top-K")


class TextUploadRequest(BaseModel):
    """文本直接上传请求"""

    content: str = Field(..., min_length=10, description="文本内容")
    source_name: str = Field(default="粘贴文本", description="来源名称")


# 支持的文件类型
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".md", ".markdown", ".txt"}
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB


@router.post("/upload", summary="上传文档到知识库")
async def upload_document(file: UploadFile = File(...)):
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

    # 3. 保存到临时文件（解析需要文件路径）
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        # 4. 解析 + 切分
        chunks = _doc_service.parse_and_split(tmp_path, file_name=filename)
        if not chunks:
            return R.fail(code=400, message="文档解析结果为空，请确认文件内容", trace_id=get_trace_id())

        # 5. 向量化 + 存入 Qdrant
        store = get_qdrant_store()
        texts = [c.content for c in chunks]
        metadatas = [c.metadata for c in chunks]
        doc_ids = store.add_documents(texts, metadatas)

        # 6. 同步到 BM25 索引（混合检索用）
        hybrid = get_hybrid_retriever()
        hybrid.add_documents(texts, metadatas)

        logger.info("[Knowledge] 文档入库成功 | file={} chunks={}", filename, len(chunks))

        return R.success(
            data={
                "filename": filename,
                "chunks_count": len(chunks),
                "doc_ids_sample": doc_ids[:3],  # 返回前 3 个 ID 作为示例
            },
            message=f"文档 '{filename}' 入库成功，共 {len(chunks)} 个片段",
            trace_id=get_trace_id(),
        )
    finally:
        # 清理临时文件
        Path(tmp_path).unlink(missing_ok=True)


@router.post("/upload_text", summary="直接上传文本到知识库")
async def upload_text(req: TextUploadRequest):
    """
    直接粘贴文本到知识库（不需要文件）

    适合小段文本、FAQ 等场景
    """
    chunks = _doc_service.parse_text_content(req.content, source_name=req.source_name)
    if not chunks:
        return R.fail(code=400, message="文本内容为空", trace_id=get_trace_id())

    store = get_qdrant_store()
    texts = [c.content for c in chunks]
    metadatas = [c.metadata for c in chunks]
    store.add_documents(texts, metadatas)

    # 同步到 BM25 索引
    hybrid = get_hybrid_retriever()
    hybrid.add_documents(texts, metadatas)

    return R.success(
        data={"source": req.source_name, "chunks_count": len(chunks)},
        message=f"文本入库成功，共 {len(chunks)} 个片段",
        trace_id=get_trace_id(),
    )


@router.post("/query", summary="知识库问答 (RAG)")
async def query_knowledge(req: KnowledgeQueryRequest):
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
        },
        trace_id=get_trace_id(),
    )


@router.get("/info", summary="知识库信息")
async def knowledge_info():
    """获取知识库基本信息（文档数量等）"""
    store = get_qdrant_store()
    info = store.get_collection_info()
    return R.success(data=info, trace_id=get_trace_id())
