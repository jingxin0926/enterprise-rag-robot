"""
聊天相关的请求/响应模型（DTO）

类似 Java 中的 Request/Response VO
"""

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """聊天请求"""

    message: str = Field(..., min_length=1, max_length=4000, description="用户消息")
    session_id: str | None = Field(default=None, description="会话 ID，不传则创建新会话")
    stream: bool = Field(default=True, description="是否流式输出（默认开启）")


class ChatResponse(BaseModel):
    """聊天响应（非流式时使用）"""

    answer: str = Field(description="AI 回答")
    session_id: str = Field(description="会话 ID")
    # Token 用量
    prompt_tokens: int = Field(default=0, description="输入 token 数")
    completion_tokens: int = Field(default=0, description="输出 token 数")
    total_tokens: int = Field(default=0, description="总 token 数")


class ClearHistoryRequest(BaseModel):
    """清空会话历史请求"""

    session_id: str = Field(..., description="要清空的会话 ID")
