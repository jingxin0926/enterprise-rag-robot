"""
文档解析与切分服务

支持格式：PDF / DOCX / Markdown / TXT
切分策略：基于 LangChain RecursiveCharacterTextSplitter

设计要点：
1. 不同格式独立解析器，统一输出纯文本
2. 切分参数可配置（chunk_size / overlap）
3. 保留元数据（文件名、页码/章节位置）
"""

from dataclasses import dataclass, field
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter
from loguru import logger


@dataclass
class DocumentChunk:
    """文档切片"""

    content: str
    metadata: dict = field(default_factory=dict)


# 切分参数（企业知识库经验值）
DEFAULT_CHUNK_SIZE = 500  # 每段约 500 字符（中文约 250 字）
DEFAULT_CHUNK_OVERLAP = 100  # 上下文重叠 100 字符，保证语义连续


class DocumentService:
    """文档解析 + 切分服务"""

    def __init__(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ) -> None:
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""],
            length_function=len,
        )

    def parse_and_split(self, file_path: str, file_name: str | None = None) -> list[DocumentChunk]:
        """
        解析文件并切分为 chunks

        Args:
            file_path: 文件路径
            file_name: 原始文件名（用于元数据）

        Returns:
            DocumentChunk 列表
        """
        path = Path(file_path)
        suffix = path.suffix.lower()
        name = file_name or path.name

        # 根据后缀选择解析器
        if suffix == ".pdf":
            text = self._parse_pdf(path)
        elif suffix in (".docx", ".doc"):
            text = self._parse_docx(path)
        elif suffix in (".md", ".markdown"):
            text = self._parse_text(path)
        elif suffix == ".txt":
            text = self._parse_text(path)
        else:
            raise ValueError(f"不支持的文件格式: {suffix}（支持 PDF/DOCX/MD/TXT）")

        if not text.strip():
            logger.warning("[DocService] 文件解析结果为空 | file={}", name)
            return []

        # 切分
        chunks = self._splitter.split_text(text)

        # 组装结果（带元数据）
        results = []
        for i, chunk in enumerate(chunks):
            results.append(
                DocumentChunk(
                    content=chunk,
                    metadata={
                        "source": name,
                        "chunk_index": i,
                        "total_chunks": len(chunks),
                    },
                )
            )

        logger.info(
            "[DocService] 文档解析完成 | file={} format={} text_len={} chunks={}",
            name,
            suffix,
            len(text),
            len(results),
        )
        return results

    def parse_text_content(self, text: str, source_name: str = "paste") -> list[DocumentChunk]:
        """
        直接解析文本内容（不需要文件）

        用于粘贴文本、API 直传等场景
        """
        if not text.strip():
            return []

        chunks = self._splitter.split_text(text)
        results = []
        for i, chunk in enumerate(chunks):
            results.append(
                DocumentChunk(
                    content=chunk,
                    metadata={
                        "source": source_name,
                        "chunk_index": i,
                        "total_chunks": len(chunks),
                    },
                )
            )
        return results

    # ------------------------------------------------------------------
    # 各格式解析器
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_pdf(path: Path) -> str:
        """PDF 解析（基于 PyMuPDF，速度快、格式保留好）"""
        import pymupdf

        text_parts = []
        with pymupdf.open(str(path)) as doc:
            for page in doc:
                text_parts.append(page.get_text())
        return "\n".join(text_parts)

    @staticmethod
    def _parse_docx(path: Path) -> str:
        """Word 文档解析"""
        from docx import Document

        doc = Document(str(path))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n".join(paragraphs)

    @staticmethod
    def _parse_text(path: Path) -> str:
        """纯文本 / Markdown 文件解析"""
        return path.read_text(encoding="utf-8")
