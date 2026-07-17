"""基于 Redis List 的单 Worker 可靠文档任务队列。"""

import json

from loguru import logger

from app.infra.cache.redis_client import get_redis

_PENDING_KEY = "smartqa:document-task:pending"
_PROCESSING_KEY = "smartqa:document-task:processing"
INDEX_REFRESH_CHANNEL = "smartqa:bm25:refresh"


class DocumentTaskQueue:
    """通过 BRPOPLPUSH 实现任务领取与确认，避免任务在 Worker 异常时直接丢失。"""

    async def enqueue(self, task_id: str) -> None:
        """将任务投递到待处理队列。"""
        redis = await get_redis()
        if redis is None:
            raise RuntimeError("Redis 不可用，无法投递异步文档任务")
        await redis.lpush(_PENDING_KEY, json.dumps({"task_id": task_id}))
        logger.info("[DocumentQueue] 任务已投递 | task_id={}", task_id)

    async def claim(self, timeout: int = 5) -> tuple[str, str] | None:
        """可靠领取一条任务，并保留原始消息以便确认。"""
        redis = await get_redis()
        if redis is None:
            raise RuntimeError("Redis 不可用，Worker 无法领取任务")
        raw = await redis.brpoplpush(_PENDING_KEY, _PROCESSING_KEY, timeout=timeout)
        if raw is None:
            return None
        payload = json.loads(raw)
        return raw, str(payload["task_id"])

    async def acknowledge(self, raw: str) -> None:
        """确认任务已处理，从处理中队列删除。"""
        redis = await get_redis()
        if redis:
            await redis.lrem(_PROCESSING_KEY, 1, raw)

    async def recover_processing_tasks(self) -> int:
        """Worker 启动时将上次异常遗留的处理中任务重新投递。"""
        redis = await get_redis()
        if redis is None:
            raise RuntimeError("Redis 不可用，无法恢复未确认任务")
        messages = await redis.lrange(_PROCESSING_KEY, 0, -1)
        if not messages:
            return 0
        async with redis.pipeline(transaction=True) as pipeline:
            pipeline.delete(_PROCESSING_KEY)
            pipeline.lpush(_PENDING_KEY, *messages)
            await pipeline.execute()
        logger.warning("[DocumentQueue] 恢复未确认任务 | count={}", len(messages))
        return len(messages)

    async def notify_index_updated(self, collection_name: str) -> None:
        """通知 Web 进程刷新对应租户的 BM25 内存索引。"""
        redis = await get_redis()
        if redis is None:
            logger.warning("[DocumentQueue] Redis 不可用，无法通知 BM25 刷新 | collection={}", collection_name)
            return
        await redis.publish(INDEX_REFRESH_CHANNEL, collection_name)
