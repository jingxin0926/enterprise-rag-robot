"""
聊天接口

功能：
1. POST /api/v1/chat  — 对话接口（支持普通和 SSE 流式）
2. POST /api/v1/chat/clear — 清空会话历史

流式输出采用 SSE (Server-Sent Events)：
- 前端可用 EventSource 或 fetch + ReadableStream 消费
- 每条 SSE event 的 data 字段为一小段文本
- 结束时发送 data: [DONE]
"""

import uuid

from fastapi import APIRouter
from loguru import logger
from sse_starlette.sse import EventSourceResponse

from app.core.response import R
from app.domain.chat_schema import ChatRequest, ChatResponse, ClearHistoryRequest
from app.middleware.trace import get_trace_id
from app.service.chat_service import ChatService

router = APIRouter(prefix="/chat", tags=["对话"])

# ChatService 实例（无状态，复用即可）
_chat_service: ChatService | None = None


def _get_chat_service() -> ChatService:
    """懒加载 ChatService（确保 LLM 客户端已初始化）"""
    global _chat_service
    if _chat_service is None:
        _chat_service = ChatService()
    return _chat_service


@router.post("", summary="对话")
async def chat(req: ChatRequest):
    """
    智能对话接口

    - stream=true（默认）：返回 SSE 流式事件
    - stream=false：返回完整 JSON 响应

    SSE 事件格式：
        event: message
        data: 一小段文字

        event: done
        data: {"session_id": "xxx", "total_tokens": 100}
    """
    # 生成或复用 session_id
    session_id = req.session_id or uuid.uuid4().hex
    service = _get_chat_service()

    # ==================== 流式输出 ====================
    if req.stream:
        async def event_generator():
            """SSE 事件生成器"""
            try:
                async for chunk in service.chat_stream(session_id, req.message):
                    # 每个 chunk 作为一条 SSE 消息发送
                    yield {"event": "message", "data": chunk}

                # 流结束，发送完成信号
                yield {
                    "event": "done",
                    "data": f'{{"session_id": "{session_id}"}}',
                }
            except Exception as e:
                logger.exception("[Chat SSE] 流式输出异常: {}", e)
                yield {"event": "error", "data": str(e)}

        return EventSourceResponse(event_generator())

    # ==================== 普通输出 ====================
    response = await service.chat(session_id, req.message)
    return R.success(
        data=ChatResponse(
            answer=response.content,
            session_id=session_id,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            total_tokens=response.total_tokens,
        ).model_dump(),
        trace_id=get_trace_id(),
    )


@router.post("/clear", summary="清空会话历史")
async def clear_history(req: ClearHistoryRequest):
    """清空指定会话的历史记录"""
    service = _get_chat_service()
    await service.clear_history(req.session_id)
    return R.success(message="会话已清空", trace_id=get_trace_id())
