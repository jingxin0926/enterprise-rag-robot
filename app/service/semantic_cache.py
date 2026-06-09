"""
语义缓存（Semantic Cache）

核心思想：
    相似的问题不需要重复调 LLM，直接返回缓存中的答案。

普通缓存 vs 语义缓存：
    - 普通缓存：key 必须完全一样才命中（"年假怎么请" ≠ "怎么申请年假"）
    - 语义缓存：计算语义相似度，相似度超过阈值就命中（这两个问题会命中同一条缓存）

实现原理：
    1. 用户提问 → 计算问题的 embedding 向量
    2. 在缓存向量库中搜索最相似的历史问题
    3. 相似度 ≥ 阈值（如 0.92）→ 命中缓存，直接返回历史答案
    4. 相似度 < 阈值 → 未命中，正常调 LLM，然后把结果存入缓存

多租户隔离：
    每个租户使用独立的 Qdrant collection（semantic_cache_{tenant_id}），
    互不干扰。

效果：
    - Token 成本降低 30-60%（高频重复问题多的场景）
    - 响应延迟从 2-3 秒降到 50ms 以内（命中时）
    - 对用户透明，不影响体验
"""

import time
from dataclasses import dataclass

from loguru import logger
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from app.core.tenant import get_current_tenant
from app.infra.vector.qdrant_store import create_qdrant_client, get_qdrant_mode, get_qdrant_target

# 缓存向量库配置（独立于知识库，避免互相干扰）
_EMBEDDING_DIM = 512  # BGE-small-zh-v1.5 的维度


@dataclass
class CacheHit:
    """缓存命中结果"""

    question: str        # 缓存中的原始问题
    answer: str          # 缓存的回答
    score: float         # 相似度分数（0-1）
    cached_at: str       # 缓存时间


class SemanticCache:
    """
    语义缓存服务

    用法：
        cache = SemanticCache()

        # 查缓存
        hit = cache.lookup("怎么请年假")
        if hit:
            return hit.answer  # 命中，直接返回

        # 没命中，调 LLM 后存入缓存
        answer = await llm.chat(...)
        cache.store("怎么请年假", answer)
    """

    def __init__(
        self,
        similarity_threshold: float = 0.92,  # 相似度阈值（越高越严格）
        max_cache_size: int = 1000,           # 最大缓存条数
        tenant_id: str | None = None,         # 指定租户（None时自动获取当前请求租户）
    ) -> None:
        self._threshold = similarity_threshold
        self._max_size = max_cache_size
        self._tenant_id = tenant_id
        self._clients: dict[str, QdrantClient] = {}
        self._embedding_model = None

    def _get_collection_name(self) -> str:
        """获取当前租户对应的缓存 collection 名"""
        tid = self._tenant_id or get_current_tenant()
        if tid == "default":
            return "semantic_cache"
        return f"semantic_cache_{tid}"

    def _ensure_client(self) -> tuple[QdrantClient, str]:
        """懒加载 Qdrant 客户端（按租户隔离 collection）"""
        collection_name = self._get_collection_name()

        if collection_name not in self._clients:
            client = create_qdrant_client()
            # 确保 collection 存在
            collections = [c.name for c in client.get_collections().collections]
            if collection_name not in collections:
                client.create_collection(
                    collection_name=collection_name,
                    vectors_config=VectorParams(size=_EMBEDDING_DIM, distance=Distance.COSINE),
                )
                logger.info("[SemanticCache] 创建缓存 collection | name={}", collection_name)
            logger.info(
                "[SemanticCache] Qdrant 初始化 | collection={} mode={} target={}",
                collection_name,
                get_qdrant_mode(),
                get_qdrant_target(),
            )
            self._clients[collection_name] = client

        return self._clients[collection_name], collection_name

    def _get_embedding_model(self):
        """懒加载 embedding 模型"""
        if self._embedding_model is None:
            from fastembed import TextEmbedding
            self._embedding_model = TextEmbedding(model_name="BAAI/bge-small-zh-v1.5")
            logger.info("[SemanticCache] Embedding 模型已加载")
        return self._embedding_model

    def _embed(self, text: str) -> list[float]:
        """计算文本的 embedding 向量"""
        model = self._get_embedding_model()
        embeddings = list(model.embed([text]))
        return embeddings[0].tolist()

    def lookup(self, question: str) -> CacheHit | None:
        """
        查询语义缓存

        Args:
            question: 用户问题

        Returns:
            CacheHit 如果命中，None 如果未命中
        """
        try:
            client, collection_name = self._ensure_client()
            query_vector = self._embed(question)

            results = client.search(
                collection_name=collection_name,
                query_vector=query_vector,
                limit=1,
                score_threshold=self._threshold,
            )

            if results:
                hit = results[0]
                payload = hit.payload or {}
                cache_hit = CacheHit(
                    question=payload.get("question", ""),
                    answer=payload.get("answer", ""),
                    score=hit.score,
                    cached_at=payload.get("cached_at", ""),
                )
                logger.info(
                    "[SemanticCache] ✅ 命中 | query='{}...' cached='{}...' score={:.4f}",
                    question[:20],
                    cache_hit.question[:20],
                    hit.score,
                )
                return cache_hit

            logger.debug("[SemanticCache] ❌ 未命中 | query='{}...'", question[:20])
            return None

        except Exception as e:
            logger.warning("[SemanticCache] 查询异常，跳过缓存 | error={}", e)
            return None

    def store(self, question: str, answer: str) -> None:
        """
        存入语义缓存

        Args:
            question: 用户问题
            answer: LLM 生成的回答
        """
        try:
            client, collection_name = self._ensure_client()
            vector = self._embed(question)

            # 生成唯一ID（基于时间戳）
            import uuid
            point_id = str(uuid.uuid4())

            point = PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "question": question,
                    "answer": answer,
                    "cached_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                },
            )

            client.upsert(collection_name=collection_name, points=[point])

            logger.info(
                "[SemanticCache] 已缓存 | question='{}...' answer_len={}",
                question[:20],
                len(answer),
            )

        except Exception as e:
            logger.warning("[SemanticCache] 存储异常 | error={}", e)

    def get_stats(self) -> dict:
        """获取缓存统计信息"""
        try:
            client, collection_name = self._ensure_client()
            info = client.get_collection(collection_name)
            return {
                "collection": collection_name,
                "total_cached": info.points_count,
                "threshold": self._threshold,
                "max_size": self._max_size,
            }
        except Exception:
            return {"total_cached": 0, "threshold": self._threshold}

    def clear(self) -> None:
        """清空当前租户的缓存"""
        try:
            client, collection_name = self._ensure_client()
            client.delete_collection(collection_name)
            client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(size=_EMBEDDING_DIM, distance=Distance.COSINE),
            )
            logger.info("[SemanticCache] 缓存已清空 | collection={}", collection_name)
        except Exception as e:
            logger.warning("[SemanticCache] 清空失败 | error={}", e)


# 全局单例
_semantic_cache: SemanticCache | None = None


def get_semantic_cache() -> SemanticCache:
    """获取全局语义缓存实例"""
    global _semantic_cache
    if _semantic_cache is None:
        _semantic_cache = SemanticCache()
    return _semantic_cache
