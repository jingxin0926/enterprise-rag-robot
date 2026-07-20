"""
Qdrant 向量库客户端

设计要点：
1. 使用 qdrant-client 内置的 fastembed 做 embedding（ONNX，无需 GPU）
2. 开发阶段可用本地文件模式（无需部署 Qdrant Server）
3. 生产环境通过 QDRANT_URL / QDRANT_HOST 连接独立 Qdrant Server
4. embedding 模型首次调用会自动下载（~100MB），后续直接使用缓存
5. 多租户隔离：每个租户使用独立的 collection（tenant_{id}_knowledge）
"""

import uuid
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PointIdsList,
    PointStruct,
    Range,
    VectorParams,
)

from app.core.config import PROJECT_ROOT, settings
from app.core.tenant import get_tenant_collection_name

# 本地持久化目录
QDRANT_STORAGE_PATH = str(PROJECT_ROOT / settings.qdrant_local_path)

# Embedding 模型配置（fastembed 内置，首次使用自动下载）
# BAAI/bge-small-zh-v1.5：中文优化，维度 512，文件仅 ~90MB，CPU 高效
EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
EMBEDDING_DIM = 512

# 默认集合名（知识库）— 保留兼容，实际使用以 tenant 为准
DEFAULT_COLLECTION = "knowledge_base"


@dataclass
class SearchResult:
    """向量检索结果（单条）"""

    content: str  # 检索到的文档片段内容
    score: float  # 向量相似度分数（0-1，越高越相关）
    metadata: dict = field(default_factory=dict)  # 元数据（来源文件名、片段索引、入库时间等）


class QdrantStore:
    """
    Qdrant 向量存储

    用法：
        store = QdrantStore()
        await store.add_documents(texts, metadatas)
        results = await store.search("问题", top_k=5)
    """

    def __init__(self, collection_name: str | None = None) -> None:
        # 多租户：优先使用传入的 collection，否则根据当前租户上下文自动获取
        self._collection_name = collection_name or get_tenant_collection_name()

        # 初始化客户端：生产连接 Qdrant Server，本地开发可使用文件模式
        self._client = create_qdrant_client()

        # 确保集合存在
        self._ensure_collection()

        logger.info(
            "✅ Qdrant 初始化 | collection={} mode={} target={} model={}",
            self._collection_name,
            get_qdrant_mode(),
            get_qdrant_target(),
            EMBEDDING_MODEL,
        )

    def _ensure_collection(self) -> None:
        """确保向量集合存在，不存在则创建"""
        collections = [c.name for c in self._client.get_collections().collections]
        if self._collection_name not in collections:
            self._client.create_collection(
                collection_name=self._collection_name,
                vectors_config=VectorParams(
                    size=EMBEDDING_DIM,
                    distance=Distance.COSINE,
                ),
            )
            logger.info("📦 创建 Qdrant 集合 | name={} dim={}", self._collection_name, EMBEDDING_DIM)

    def _get_embeddings(self, texts: list[str]) -> list[list[float]]:
        """
        使用 fastembed 生成向量

        fastembed 基于 ONNX Runtime，CPU 推理效率高
        """
        from fastembed import TextEmbedding

        # fastembed 会自动缓存模型，首次调用下载
        model = TextEmbedding(model_name=EMBEDDING_MODEL)
        embeddings = list(model.embed(texts))
        return [emb.tolist() for emb in embeddings]

    def add_documents(
        self,
        texts: list[str],
        metadatas: list[dict] | None = None,
    ) -> list[str]:
        """
        批量添加文档到向量库

        Args:
            texts: 文本片段列表
            metadatas: 每个片段的元数据（文件名、页码等）

        Returns:
            生成的文档 ID 列表
        """
        if not texts:
            return []

        # 生成 embedding
        logger.info("[Qdrant] 正在向量化 {} 个片段...", len(texts))
        embeddings = self._get_embeddings(texts)

        # 构造 points
        ids = [uuid.uuid4().hex for _ in texts]
        points = []
        for i, (text, embedding, doc_id) in enumerate(zip(texts, embeddings, ids, strict=True)):
            payload = {"content": text}
            if metadatas and i < len(metadatas):
                payload.update(metadatas[i])
            points.append(
                PointStruct(
                    id=doc_id,
                    vector=embedding,
                    payload=payload,
                )
            )

        # 批量写入
        self._client.upsert(
            collection_name=self._collection_name,
            points=points,
        )
        logger.info("[Qdrant] ✅ 写入 {} 个向量 | collection={}", len(points), self._collection_name)
        return ids

    def search(self, query: str, top_k: int = 5, score_threshold: float = 0.3) -> list[SearchResult]:
        """
        向量相似度检索

        Args:
            query: 查询文本
            top_k: 返回前 K 条
            score_threshold: 最低相似度阈值（过滤低质量结果）

        Returns:
            SearchResult 列表（按相似度降序）
        """
        # 查询向量化
        query_embedding = self._get_embeddings([query])[0]

        # 执行检索（qdrant-client 1.12+ 使用 query_points）
        results = self._client.query_points(
            collection_name=self._collection_name,
            query=query_embedding,
            limit=top_k,
            score_threshold=score_threshold,
        ).points

        search_results = []
        for hit in results:
            search_results.append(
                SearchResult(
                    content=hit.payload.get("content", ""),
                    score=hit.score,
                    metadata={k: v for k, v in hit.payload.items() if k != "content"},
                )
            )

        logger.info(
            "[Qdrant] 检索完成 | query='{}...' top_k={} hits={}",
            query[:20],
            top_k,
            len(search_results),
        )
        return search_results

    def get_collection_info(self) -> dict:
        """获取集合信息（文档数量等）"""
        info = self._client.get_collection(self._collection_name)
        return {
            "name": self._collection_name,
            "points_count": info.points_count,
            "status": info.status.value if info.status else "unknown",
        }

    def delete_collection(self) -> None:
        """删除集合（慎用）"""
        self._client.delete_collection(self._collection_name)
        logger.warning("[Qdrant] 删除集合 | name={}", self._collection_name)

    def delete_points(self, point_ids: list[str]) -> None:
        """按向量点 ID 删除，供入库补偿事务使用。"""
        if not point_ids:
            return
        self._client.delete(
            collection_name=self._collection_name,
            points_selector=PointIdsList(points=point_ids),
            wait=True,
        )
        logger.info("[Qdrant] 删除向量点 | collection={} count={}", self._collection_name, len(point_ids))

    def delete_by_document_id(self, document_id: str) -> None:
        """按文档关联键删除所有向量切片。"""
        self._client.delete(
            collection_name=self._collection_name,
            points_selector=FilterSelector(
                filter=Filter(must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))])
            ),
            wait=True,
        )
        logger.info("[Qdrant] 删除文档向量 | collection={} document_id={}", self._collection_name, document_id)

    def get_neighbor_chunks(self, document_id: str, chunk_index: int, window: int = 1) -> list[SearchResult]:
        """读取同一文档中命中片段附近的内容，用于补全结构化文档的局部上下文。"""
        if window <= 0:
            return []

        lower_bound = max(0, chunk_index - window)
        upper_bound = chunk_index + window
        points, _ = self._client.scroll(
            collection_name=self._collection_name,
            scroll_filter=Filter(
                must=[
                    FieldCondition(key="document_id", match=MatchValue(value=document_id)),
                    FieldCondition(key="chunk_index", range=Range(gte=lower_bound, lte=upper_bound)),
                ]
            ),
            limit=window * 2 + 1,
            with_payload=True,
            with_vectors=False,
        )
        chunks = [
            SearchResult(
                content=(point.payload or {}).get("content", ""),
                score=0.0,
                metadata={key: value for key, value in (point.payload or {}).items() if key != "content"},
            )
            for point in points
            if (point.payload or {}).get("content")
        ]
        return sorted(chunks, key=lambda item: int(item.metadata.get("chunk_index", 0)))

    def list_legacy_points(self, batch_size: int = 256) -> list[dict]:
        """读取尚未关联 document_id 的历史向量 payload。"""
        points: list[dict] = []
        offset = None
        while True:
            batch, next_offset = self._client.scroll(
                collection_name=self._collection_name,
                limit=batch_size,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in batch:
                payload = dict(point.payload or {})
                if payload.get("content") and not payload.get("document_id"):
                    points.append({"point_id": str(point.id), "payload": payload})
            if next_offset is None:
                break
            offset = next_offset
        return points

    def assign_document_id(self, point_ids: list[str], document_id: str) -> None:
        """为已有向量补充文档关联键，不重算 embedding。"""
        if not point_ids:
            return
        self._client.set_payload(
            collection_name=self._collection_name,
            payload={"document_id": document_id, "legacy": True},
            points=point_ids,
            wait=True,
        )
        logger.info("[Qdrant] 回填文档关联键 | collection={} document_id={} count={}", self._collection_name, document_id, len(point_ids))


# 按 collection 名称缓存实例（多租户各自持有独立实例）
_qdrant_stores: dict[str, QdrantStore] = {}


def get_qdrant_store(collection_name: str | None = None) -> QdrantStore:
    """
    获取 Qdrant 实例（按租户隔离）

    不传 collection_name 时，自动使用当前请求的租户 collection。
    相同 collection 复用同一个实例。
    """
    name = collection_name or get_tenant_collection_name()
    if name not in _qdrant_stores:
        _qdrant_stores[name] = QdrantStore(collection_name=name)
    return _qdrant_stores[name]


def get_qdrant_mode() -> str:
    """返回当前 Qdrant 连接模式。"""
    return "server" if settings.use_qdrant_server else "local"


def get_qdrant_target() -> str:
    """返回当前 Qdrant 连接目标，用于日志和排障。"""
    return settings.qdrant_server_url or QDRANT_STORAGE_PATH


def create_qdrant_client() -> QdrantClient:
    """创建 Qdrant 客户端，按配置自动选择 Server 或本地文件模式。"""
    if settings.use_qdrant_server:
        return QdrantClient(
            url=settings.qdrant_server_url,
            api_key=settings.qdrant_api_key or None,
        )

    Path(QDRANT_STORAGE_PATH).mkdir(parents=True, exist_ok=True)
    return QdrantClient(path=QDRANT_STORAGE_PATH)


def get_qdrant_client() -> QdrantClient:
    """获取底层 Qdrant 客户端（用于启动重建等运维操作）"""
    return create_qdrant_client()
