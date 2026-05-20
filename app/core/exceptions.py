"""
异常体系

设计要点：
1. 定义业务异常基类 BizException，类似 Java 的自定义 RuntimeException
2. 通过错误码（ErrorCode）枚举集中管理，避免散落在各处的魔法字符串
3. 错误码采用 6 位数字：前 3 位模块码 + 后 3 位错误序号
   - 000xxx：通用
   - 001xxx：认证授权
   - 002xxx：LLM 相关
   - 003xxx：知识库 / RAG
   - 004xxx：限流配额
"""

from enum import Enum
from typing import Any


class ErrorCode(int, Enum):
    """
    错误码枚举（与业务模块对齐）

    使用方式：raise BizException(ErrorCode.LLM_TIMEOUT, "DeepSeek 调用超时")
    """

    # ----- 通用 000xxx -----
    SUCCESS = 0
    UNKNOWN_ERROR = 100
    PARAM_INVALID = 400
    NOT_FOUND = 404
    INTERNAL_ERROR = 500

    # ----- 认证授权 001xxx -----
    UNAUTHORIZED = 1001
    FORBIDDEN = 1002
    TOKEN_EXPIRED = 1003

    # ----- LLM 002xxx -----
    LLM_API_ERROR = 2001
    LLM_TIMEOUT = 2002
    LLM_RATE_LIMITED = 2003
    LLM_QUOTA_EXCEEDED = 2004

    # ----- 知识库 / RAG 003xxx -----
    KB_NOT_FOUND = 3001
    DOC_PARSE_FAILED = 3002
    EMBEDDING_FAILED = 3003
    VECTOR_SEARCH_FAILED = 3004

    # ----- 限流 / 配额 004xxx -----
    RATE_LIMIT = 4001
    QUOTA_EXCEEDED = 4002


class BizException(Exception):
    """
    业务异常基类

    用法：
        raise BizException(ErrorCode.PARAM_INVALID, "用户ID不能为空")
        raise BizException(ErrorCode.LLM_TIMEOUT, "DeepSeek 超时", data={"retry": 3})
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str | None = None,
        data: Any = None,
    ) -> None:
        self.code: int = code.value
        self.message: str = message or code.name
        self.data: Any = data
        super().__init__(self.message)

    def __str__(self) -> str:
        return f"[BizException {self.code}] {self.message}"


# 常用快捷异常类（语义更清晰）
class ParamInvalidException(BizException):
    """参数非法"""

    def __init__(self, message: str = "参数非法", data: Any = None) -> None:
        super().__init__(ErrorCode.PARAM_INVALID, message, data)


class NotFoundException(BizException):
    """资源不存在"""

    def __init__(self, message: str = "资源不存在", data: Any = None) -> None:
        super().__init__(ErrorCode.NOT_FOUND, message, data)


class UnauthorizedException(BizException):
    """未认证"""

    def __init__(self, message: str = "未认证", data: Any = None) -> None:
        super().__init__(ErrorCode.UNAUTHORIZED, message, data)
