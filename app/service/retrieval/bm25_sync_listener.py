"""跨进程 BM25 索引刷新监听器。"""

import asyncio
from contextlib import suppress

from loguru import logger

from app.infra.cache.redis_client import get_redis
from app.infra.queue.document_task_queue import INDEX_REFRESH_CHANNEL
from app.infra.vector.qdrant_store import get_qdrant_client
from app.service.retrieval.bm25_rebuild import _rebuild_single_collection


async def run_bm25_sync_listener() -> None:
    """订阅 Worker 索引变更事件，仅重建被影响 collection 的 BM25。"""
    redis = await get_redis()
    if redis is None:
        logger.warning("[BM25-Sync] Redis 不可用，跳过跨进程索引刷新监听")
        return

    pubsub = redis.pubsub()
    await pubsub.subscribe(INDEX_REFRESH_CHANNEL)
    logger.info("[BM25-Sync] 监听已启动 | channel={}", INDEX_REFRESH_CHANNEL)
    try:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message.get("type") == "message":
                collection_name = str(message["data"])
                count = _rebuild_single_collection(get_qdrant_client(), collection_name)
                logger.info("[BM25-Sync] 索引刷新完成 | collection={} docs={}", collection_name, count)
            await asyncio.sleep(0.1)
    finally:
        with suppress(Exception):
            await pubsub.unsubscribe(INDEX_REFRESH_CHANNEL)
            await pubsub.aclose()
