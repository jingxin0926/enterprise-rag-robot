"""
Rerank 重排序

设计要点：
1. 使用 fastembed 内置的 cross-encoder 模型
2. ONNX 推理，CPU 友好（不需要 GPU）
3. 对初步检索结果精排，显著提升前几条的准确度
4. 模型首次使用自动下载缓存
"""

from dataclasses import dataclass

from loguru import logger


@dataclass
class RerankResult:
    """重排序结果（单条）"""

    content: str           # 文档片段内容
    score: float           # Cross-Encoder 相关性分数（越高越相关）
    metadata: dict         # 元数据（来源文件名等）
    original_index: int    # 在输入列表中的原始位置（用于溯源）


class Reranker:
    """
    Cross-Encoder 重排器

    用法：
        reranker = Reranker()
        results = reranker.rerank(query, documents, top_k=5)
    """

    def __init__(self, model_name: str = "Xenova/ms-marco-MiniLM-L-6-v2") -> None:
        self._model_name = model_name
        self._model = None

    def _get_model(self):
        """懒加载 rerank 模型"""
        if self._model is None:
            from fastembed import TextCrossEncoder
            self._model = TextCrossEncoder(model_name=self._model_name)
            logger.info("[Reranker] 模型加载完成 | model={}", self._model_name)
        return self._model

    def rerank(
        self,
        query: str,
        documents: list[str],
        metadatas: list[dict] | None = None,
        top_k: int = 5,
    ) -> list[RerankResult]:
        """
        重排序

        Args:
            query: 查询文本
            documents: 候选文档列表
            metadatas: 对应的元数据
            top_k: 返回前 K 条

        Returns:
            RerankResult 列表（按相关性降序）
        """
        if not documents:
            return []

        model = self._get_model()

        # 构造 (query, doc) 对（fastembed rerank 内部处理）
        # 计算相关性分数
        scores = list(model.rerank(query, documents))

        # 按分数排序
        scored_items = sorted(
            enumerate(scores), key=lambda x: x[1], reverse=True
        )[:top_k]

        results = []
        for idx, score in scored_items:
            meta = metadatas[idx] if metadatas and idx < len(metadatas) else {}
            results.append(
                RerankResult(
                    content=documents[idx],
                    score=float(score),
                    metadata=meta,
                    original_index=idx,
                )
            )

        logger.info(
            "[Reranker] 重排完成 | query='{}...' input={} output={}",
            query[:20],
            len(documents),
            len(results),
        )
        return results
