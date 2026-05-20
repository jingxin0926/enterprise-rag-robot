"""
统一响应封装

设计要点：
1. 类似 Spring Boot 中的 Result<T> / R<T>，所有接口返回统一结构
2. 三个核心字段：code / message / data，外加 trace_id 便于追溯
3. 提供 success / fail 静态方法，方便业务调用
"""

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from app.core.exceptions import ErrorCode

T = TypeVar("T")


class R(BaseModel, Generic[T]):
    """
    统一 API 响应模型

    示例：
        return R.success(data={"user_id": 1})
        return R.fail(ErrorCode.PARAM_INVALID, "用户名不能为空")
    """

    code: int = Field(default=0, description="业务状态码，0 表示成功")
    message: str = Field(default="success", description="提示信息")
    data: T | None = Field(default=None, description="业务数据")
    trace_id: str | None = Field(default=None, description="链路追踪 ID")
    timestamp: int = Field(
        default_factory=lambda: int(datetime.now().timestamp() * 1000),
        description="毫秒时间戳",
    )

    # ----------- 工厂方法 -----------
    @classmethod
    def success(
        cls,
        data: Any = None,
        message: str = "success",
        trace_id: str | None = None,
    ) -> "R[Any]":
        """构造成功响应"""
        return cls(code=ErrorCode.SUCCESS.value, message=message, data=data, trace_id=trace_id)

    @classmethod
    def fail(
        cls,
        code: ErrorCode | int = ErrorCode.UNKNOWN_ERROR,
        message: str = "error",
        data: Any = None,
        trace_id: str | None = None,
    ) -> "R[Any]":
        """构造失败响应"""
        code_value = code.value if isinstance(code, ErrorCode) else code
        return cls(code=code_value, message=message, data=data, trace_id=trace_id)
