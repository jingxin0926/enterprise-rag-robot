"""
BM25 索引启动重建

解决问题：
    BM25 是纯内存索引，服务重启后丢失。
    重启后如果不重建，混合检索会退化为纯向量检索（只有 Qdrant 有数据）。

方案：
    启动时扫描 Qdrant 中所有 knowledge 类型的 collection，
    逐个读取文档内容，重建对应租户的 BM25 索引。

性能：
    - 1000 篇文档约 2-3 秒（jieba 分词为主）
    - 不阻塞请求（在 lifespan 启动阶段执行）
"""

from loguru import logger
from qdrant_client import QdrantClient

from app.infra.vector.qdrant_store import get_qdrant_client, get_qdrant_mode, get_qdrant_target
from app.service.retrieval.hybrid_retriever import get_hybrid_retriever

# 需要重建 BM25 的 collection 名称模式
_KNOWLEDGE_COLLECTION_PREFIX = "tenant_"
_KNOWLEDGE_COLLECTION_SUFFIX = "_knowledge"
_DEFAULT_COLLECTION = "knowledge_base"

# 每次 scroll 取的 batch 大小
_SCROLL_BATCH_SIZE = 100


async def rebuild_bm25_from_qdrant() -> None:
    """
    从 Qdrant 重建所有租户的 BM25 索引

    在应用启动时调用（lifespan），确保混合检索可用。
    """
    try:
        client = get_qdrant_client()
        collections = [c.name for c in client.get_collections().collections]
    except Exception as e:
        logger.warning(
            "[BM25-Rebuild] 无法连接 Qdrant，跳过重建 | mode={} target={} error={}",
            get_qdrant_mode(),
            get_qdrant_target(),
            e,
        )
        return

    logger.info(
        "[BM25-Rebuild] 扫描 Qdrant | mode={} target={} collections={}",
        get_qdrant_mode(),
        get_qdrant_target(),
        len(collections),
    )

    # 筛选出知识库 collection（default + 各租户）
    knowledge_collections = []
    for name in collections:
        if name == _DEFAULT_COLLECTION:
            knowledge_collections.append(name)
        elif name.startswith(_KNOWLEDGE_COLLECTION_PREFIX) and name.endswith(_KNOWLEDGE_COLLECTION_SUFFIX):
            knowledge_collections.append(name)

    if not knowledge_collections:
        logger.info("[BM25-Rebuild] 没有知识库 collection，跳过重建")
        return

    total_docs = 0
    for collection_name in knowledge_collections:
        docs_count = _rebuild_single_collection(client, collection_name)
        total_docs += docs_count

    logger.info(
        "[BM25-Rebuild] ✅ 重建完成 | collections={} total_docs={}",
        len(knowledge_collections),
        total_docs,
    )


def _rebuild_single_collection(client: QdrantClient, collection_name: str) -> int:
    """
    重建单个 collection 的 BM25 索引

    Args:
        client: Qdrant 客户端
        collection_name: 集合名称

    Returns:
        重建的文档数量
    """
    texts: list[str] = []
    metadatas: list[dict] = []

    # 使用 scroll 分批读取所有文档
    offset = None
    while True:
        results, next_offset = client.scroll(
            collection_name=collection_name,
            limit=_SCROLL_BATCH_SIZE,
            offset=offset,
            with_payload=True,
            with_vectors=False,  # 不需要向量，只要 payload
        )

        if not results:
            break

        for point in results:
            payload = point.payload or {}
            content = payload.get("content", "")
            if content:
                texts.append(content)
                # 提取除 content 外的元数据
                meta = {k: v for k, v in payload.items() if k != "content"}
                metadatas.append(meta)

        if next_offset is None:
            break
        offset = next_offset

    if not texts:
        logger.debug("[BM25-Rebuild] collection '{}' 无文档，跳过", collection_name)
        return 0

    # 获取对应的 HybridRetriever 并重建 BM25
    retriever = get_hybrid_retriever(collection_name=collection_name)
    retriever._bm25.rebuild_from_data(texts, metadatas)

    logger.info(
        "[BM25-Rebuild] 重建索引 | collection={} docs={}",
        collection_name,
        len(texts),
    )
    return len(texts)
