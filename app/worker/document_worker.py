"""文档入库 Redis Worker 入口。"""

import asyncio

from loguru import logger

from app.core.config import settings
from app.core.logger import setup_logger
from app.infra.cache.redis_client import close_redis, get_redis
from app.infra.database.database import close_database, init_database
from app.infra.queue.document_task_queue import DocumentTaskQueue
from app.service.knowledge.document_ingest_service import DocumentIngestService


async def run_worker() -> None:
    """持续消费文档入库任务，Worker 重启时先恢复未确认任务。"""
    setup_logger()
    logger.info("[DocumentWorker] 启动 | env={}", settings.app_env.value)
    await get_redis()
    await init_database()

    queue = DocumentTaskQueue()
    service = DocumentIngestService()
    await queue.recover_processing_tasks()

    try:
        while True:
            claimed = await queue.claim(timeout=5)
            if claimed is None:
                continue
            raw, task_id = claimed
            try:
                result = await service.process_task(task_id)
                if result.should_retry:
                    await queue.enqueue(task_id)
                if result.index_collection:
                    await queue.notify_index_updated(result.index_collection)
            except Exception:
                logger.exception("[DocumentWorker] 未预期异常 | task_id={}", task_id)
                # 保留在 processing 列表，由下次 Worker 启动恢复，避免未知异常时无限快速重试。
                continue
            await queue.acknowledge(raw)
    finally:
        await close_database()
        await close_redis()


def main() -> None:
    """控制台入口。"""
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
