"""
Token 用量追踪与计费

设计要点：
1. 记录每次 LLM 调用的 token 消耗
2. 按租户/用户汇总（多租户成本核算）
3. 当前用内存存储，生产接 MySQL 持久化
4. 为后续的配额管理、账单系统做基础
"""

from dataclasses import dataclass, field
from datetime import datetime
from collections import defaultdict

from loguru import logger


@dataclass
class TokenRecord:
    """单次 token 消费记录"""

    tenant_id: str
    user_id: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    endpoint: str  # 哪个接口消费的（chat/agent/rag）
    timestamp: datetime = field(default_factory=datetime.now)


class TokenTracker:
    """
    Token 用量追踪器

    用法：
        tracker = get_token_tracker()
        tracker.record(tenant_id="t1", user_id="u1", model="deepseek-chat",
                       prompt_tokens=100, completion_tokens=50, endpoint="agent")
        stats = tracker.get_tenant_stats("t1")
    """

    def __init__(self) -> None:
        # 内存存储（按租户汇总）
        self._records: list[TokenRecord] = []
        self._tenant_totals: dict[str, dict[str, int]] = defaultdict(
            lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "call_count": 0}
        )
        self._user_totals: dict[str, dict[str, int]] = defaultdict(
            lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "call_count": 0}
        )

    def record(
        self,
        tenant_id: str,
        user_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        endpoint: str = "unknown",
    ) -> None:
        """记录一次 token 消费"""
        total = prompt_tokens + completion_tokens

        record = TokenRecord(
            tenant_id=tenant_id,
            user_id=user_id,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total,
            endpoint=endpoint,
        )
        self._records.append(record)

        # 租户维度汇总
        self._tenant_totals[tenant_id]["prompt_tokens"] += prompt_tokens
        self._tenant_totals[tenant_id]["completion_tokens"] += completion_tokens
        self._tenant_totals[tenant_id]["total_tokens"] += total
        self._tenant_totals[tenant_id]["call_count"] += 1

        # 用户维度汇总
        self._user_totals[user_id]["prompt_tokens"] += prompt_tokens
        self._user_totals[user_id]["completion_tokens"] += completion_tokens
        self._user_totals[user_id]["total_tokens"] += total
        self._user_totals[user_id]["call_count"] += 1

        logger.debug(
            "[TokenTracker] tenant={} user={} model={} tokens={}",
            tenant_id,
            user_id,
            model,
            total,
        )

    def get_tenant_stats(self, tenant_id: str) -> dict:
        """获取租户级统计"""
        stats = self._tenant_totals.get(tenant_id, {})
        # 估算费用（DeepSeek V3 价格）
        prompt_cost = stats.get("prompt_tokens", 0) * 0.5 / 1_000_000  # ¥0.5/百万
        completion_cost = stats.get("completion_tokens", 0) * 2.0 / 1_000_000  # ¥2/百万
        return {
            **stats,
            "estimated_cost_rmb": round(prompt_cost + completion_cost, 6),
        }

    def get_user_stats(self, user_id: str) -> dict:
        """获取用户级统计"""
        return dict(self._user_totals.get(user_id, {}))

    def get_global_stats(self) -> dict:
        """全局统计"""
        total_tokens = sum(s["total_tokens"] for s in self._tenant_totals.values())
        total_calls = sum(s["call_count"] for s in self._tenant_totals.values())
        return {
            "total_tokens": total_tokens,
            "total_calls": total_calls,
            "tenants": len(self._tenant_totals),
            "users": len(self._user_totals),
        }


# 全局单例
_tracker: TokenTracker | None = None


def get_token_tracker() -> TokenTracker:
    """获取全局 Token 追踪器"""
    global _tracker
    if _tracker is None:
        _tracker = TokenTracker()
    return _tracker
