"""
DeepSeek LLM 客户端

设计要点：
1. 基于 openai SDK（DeepSeek 完全兼容 OpenAI 协议），零额外学习成本
2. 支持同步和异步、普通输出和流式输出
3. 封装 token 用量统计，为后续计费做基础
4. 统一异常处理，将 OpenAI SDK 异常转为 BizException
5. 节省你的 10 块钱：默认 max_tokens=1024，temperature=0.3（少废话）
"""

from collections.abc import AsyncGenerator
from dataclasses import dataclass

from loguru import logger
from openai import AsyncOpenAI, APITimeoutError, RateLimitError, APIError

from app.core.config import settings
from app.core.exceptions import BizException, ErrorCode


@dataclass
class ChatMessage:
    """聊天消息（单条）"""

    role: str      # 角色：system(系统提示) / user(用户输入) / assistant(AI回答)
    content: str   # 消息内容


@dataclass
class ChatResponse:
    """LLM 响应封装"""

    content: str                 # AI 回答的文本内容
    prompt_tokens: int = 0       # 输入消耗的 token 数（问题+上下文）
    completion_tokens: int = 0   # 输出消耗的 token 数（AI回答）
    total_tokens: int = 0        # 总消耗 token 数（用于计费）
    model: str = ""              # 实际使用的模型名称


class DeepSeekClient:
    """
    DeepSeek LLM 客户端（异步）

    用法：
        client = DeepSeekClient()
        # 普通对话
        response = await client.chat(messages)
        # 流式对话
        async for chunk in client.chat_stream(messages):
            print(chunk, end="")
    """

    def __init__(self) -> None:
        """初始化异步 OpenAI 客户端（指向 DeepSeek 端点）"""
        self._client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            timeout=settings.deepseek_timeout,
            max_retries=2,  # SDK 内置重试，超时/5xx 自动重试 2 次
        )
        self._model = settings.deepseek_model
        self._temperature = settings.deepseek_temperature
        self._max_tokens = settings.deepseek_max_tokens

        logger.info(
            "✅ DeepSeek 客户端初始化 | model={} temperature={} max_tokens={}",
            self._model,
            self._temperature,
            self._max_tokens,
        )

    async def chat(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResponse:
        """
        普通对话（非流式）

        Args:
            messages: 消息列表（含 system prompt + 历史 + 当前问题）
            model: 可选指定模型，默认用配置中的
            temperature: 可选覆盖温度
            max_tokens: 可选覆盖最大 token

        Returns:
            ChatResponse 包含回答内容和 token 用量
        """
        try:
            # 将 ChatMessage 转为 OpenAI SDK 需要的 dict 格式
            raw_messages = [{"role": m.role, "content": m.content} for m in messages]

            response = await self._client.chat.completions.create(
                model=model or self._model,
                messages=raw_messages,
                temperature=temperature if temperature is not None else self._temperature,
                max_tokens=max_tokens or self._max_tokens,
                stream=False,
            )

            # 提取响应内容
            choice = response.choices[0]
            usage = response.usage

            result = ChatResponse(
                content=choice.message.content or "",
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
                model=response.model,
            )

            logger.info(
                "[LLM] 普通对话完成 | model={} tokens=({}/{}/{})",
                result.model,
                result.prompt_tokens,
                result.completion_tokens,
                result.total_tokens,
            )
            return result

        except APITimeoutError as e:
            logger.error("[LLM] 请求超时: {}", e)
            raise BizException(ErrorCode.LLM_TIMEOUT, "DeepSeek 请求超时，请稍后重试") from e
        except RateLimitError as e:
            logger.error("[LLM] 触发限流: {}", e)
            raise BizException(ErrorCode.LLM_RATE_LIMITED, "请求频率过高，请稍后重试") from e
        except APIError as e:
            logger.error("[LLM] API 错误: status={} message={}", e.status_code, e.message)
            raise BizException(ErrorCode.LLM_API_ERROR, f"DeepSeek API 错误: {e.message}") from e

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        流式对话（SSE）

        逐 token 返回内容，前端可实时展示打字效果。

        Args:
            messages: 消息列表
            model / temperature / max_tokens: 可选覆盖

        Yields:
            每次产出一小段文本（通常 1-5 个 token 对应的文字）
        """
        try:
            raw_messages = [{"role": m.role, "content": m.content} for m in messages]

            stream = await self._client.chat.completions.create(
                model=model or self._model,
                messages=raw_messages,
                temperature=temperature if temperature is not None else self._temperature,
                max_tokens=max_tokens or self._max_tokens,
                stream=True,
            )

            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        except APITimeoutError as e:
            logger.error("[LLM] 流式请求超时: {}", e)
            raise BizException(ErrorCode.LLM_TIMEOUT, "DeepSeek 请求超时") from e
        except RateLimitError as e:
            logger.error("[LLM] 流式触发限流: {}", e)
            raise BizException(ErrorCode.LLM_RATE_LIMITED, "请求频率过高") from e
        except APIError as e:
            logger.error("[LLM] 流式 API 错误: {}", e.message)
            raise BizException(ErrorCode.LLM_API_ERROR, f"DeepSeek API 错误: {e.message}") from e

    async def close(self) -> None:
        """关闭客户端连接"""
        await self._client.close()
        logger.info("DeepSeek 客户端已关闭")


# 全局单例（在 lifespan 中初始化和关闭）
deepseek_client: DeepSeekClient | None = None


def get_deepseek_client() -> DeepSeekClient:
    """获取全局 LLM 客户端实例"""
    global deepseek_client
    if deepseek_client is None:
        deepseek_client = DeepSeekClient()
    return deepseek_client
