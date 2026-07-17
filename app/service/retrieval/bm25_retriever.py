"""
BM25 全文检索（基于 jieba 中文分词）

设计要点：
1. 纯内存实现，无需 Elasticsearch（开发阶段轻量）
2. 使用 jieba 分词（中文 BM25 必须先分词）
3. 索引与 Qdrant 数据同步（共享相同的 chunks）
4. 多租户隔离：每个租户维护独立的 BM25 索引
5. 启动重建：服务重启后从 Qdrant 自动恢复索引
6. 后续上生产可替换为 ES，接口一致
"""

from dataclasses import dataclass, field

import jieba
from loguru import logger
from rank_bm25 import BM25Okapi


@dataclass
class BM25Result:
    """BM25 检索结果"""

    content: str
    score: float
    index: int  # 原始文档中的位置索引
    metadata: dict = field(default_factory=dict)


class BM25Retriever:
    """
    BM25 全文检索器

    用法：
        retriever = BM25Retriever()
        retriever.add_documents(texts, metadatas)
        results = retriever.search("问题", top_k=5)
    """

    def __init__(self) -> None:
        self._documents: list[str] = []
        self._metadatas: list[dict] = []
        self._tokenized_corpus: list[list[str]] = []
        self._bm25: BM25Okapi | None = None

    def _tokenize(self, text: str) -> list[str]:
        """中文分词（jieba）"""
        # cut_for_search 模式：精确+全模式结合，适合搜索引擎
        return list(jieba.cut_for_search(text))

    def add_documents(self, texts: list[str], metadatas: list[dict] | None = None) -> None:
        """
        添加文档到 BM25 索引

        注意：每次 add 都会重建索引（适合中小规模）
        """
        if not texts:
            return

        self._documents.extend(texts)
        if metadatas:
            self._metadatas.extend(metadatas)
        else:
            self._metadatas.extend([{}] * len(texts))

        # 对所有文档进行分词
        self._tokenized_corpus = [self._tokenize(doc) for doc in self._documents]

        # 重建 BM25 索引
        self._bm25 = BM25Okapi(self._tokenized_corpus)

        logger.info("[BM25] 索引更新 | 文档总数={}", len(self._documents))

    def rebuild_from_data(self, texts: list[str], metadatas: list[dict] | None = None) -> None:
        """
        从已有数据重建索引（启动恢复专用）

        与 add_documents 不同：先清空再重建，不追加
        """
        self._documents = []
        self._metadatas = []
        self._tokenized_corpus = []
        self._bm25 = None

        if texts:
            self.add_documents(texts, metadatas)

    def remove_by_document_id(self, document_id: str) -> int:
        """删除指定文档的内存切片并重建 BM25 索引。"""
        retained = [
            (text, metadata)
            for text, metadata in zip(self._documents, self._metadatas, strict=True)
            if metadata.get("document_id") != document_id
        ]
        removed_count = len(self._documents) - len(retained)
        if not removed_count:
            return 0

        self._documents = []
        self._metadatas = []
        self._tokenized_corpus = []
        self._bm25 = None
        if retained:
            texts, metadatas = zip(*retained, strict=True)
            self.add_documents(list(texts), list(metadatas))
        logger.info("[BM25] 删除文档切片 | document_id={} count={}", document_id, removed_count)
        return removed_count

    def search(self, query: str, top_k: int = 5) -> list[BM25Result]:
        """
        BM25 检索

        Args:
            query: 查询文本
            top_k: 返回前 K 条

        Returns:
            BM25Result 列表（按分数降序）
        """
        if not self._bm25 or not self._documents:
            return []

        # 查询分词
        tokenized_query = self._tokenize(query)

        # 获取所有文档分数
        scores = self._bm25.get_scores(tokenized_query)

        # 按分数排序取 Top-K
        scored_indices = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]

        results = []
        for idx, score in scored_indices:
            if score <= 0:
                continue  # 跳过无关文档
            results.append(
                BM25Result(
                    content=self._documents[idx],
                    score=float(score),
                    index=idx,
                    metadata=self._metadatas[idx] if idx < len(self._metadatas) else {},
                )
            )

        logger.info(
            "[BM25] 检索完成 | query='{}...' hits={}",
            query[:20],
            len(results),
        )
        return results

    @property
    def doc_count(self) -> int:
        """文档总数"""
        return len(self._documents)
