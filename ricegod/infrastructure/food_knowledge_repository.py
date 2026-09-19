from pathlib import Path
from typing import Any
import logging

from langchain_text_splitters import RecursiveCharacterTextSplitter

from ricegod.config import settings
from ricegod.infrastructure.embedding_client import OpenAICompatibleEmbeddingClient
from ricegod.infrastructure.milvus_vector_store import MilvusVectorStore
from ricegod.infrastructure.rerank_client import DashScopeRerankClient


logger = logging.getLogger(__name__)


class FoodKnowledgeRepository:
    """Ingest and search food culture knowledge with Milvus."""

    def __init__(
        self,
        embedder: OpenAICompatibleEmbeddingClient | None = None,
        vector_store: MilvusVectorStore | None = None,
        reranker: DashScopeRerankClient | None = None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> None:
        self.embedder = embedder or OpenAICompatibleEmbeddingClient()
        self.vector_store = vector_store or MilvusVectorStore()
        self.reranker = reranker
        if self.reranker is None:
            try:
                self.reranker = DashScopeRerankClient()
            except Exception:
                logger.exception("Failed to initialize DashScope reranker; using vector search ordering.")
        self.chunk_size = chunk_size if chunk_size is not None else settings.KB_CHUNK_SIZE
        self.chunk_overlap = chunk_overlap if chunk_overlap is not None else settings.KB_CHUNK_OVERLAP
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            separators=["\n\n", "\n", "。", "；", "，", "、", " "],
            length_function=len,
        )

    def split_text(self, text: str) -> list[str]:
        return [chunk.strip() for chunk in self.splitter.split_text(text) if chunk.strip()]

    def build_rows(self, chunks: list[str], source: str) -> list[dict[str, Any]]:
        return [
            {
                "id": f"{source}_{index}",
                "content": chunk,
                "source": source,
                "chunk_index": index,
            }
            for index, chunk in enumerate(chunks, start=1)
        ]

    def ingest_file(
        self,
        path: str | Path,
        *,
        clear: bool = False,
        batch_size: int = 32,
    ) -> int:
        file_path = Path(path)
        text = file_path.read_text(encoding="utf-8")
        chunks = self.split_text(text)
        rows = self.build_rows(chunks, file_path.name)
        if clear:
            self.vector_store.clear()

        total = 0
        for start in range(0, len(rows), batch_size):
            batch_rows = rows[start : start + batch_size]
            embeddings = self.embedder.embed_documents([row["content"] for row in batch_rows])
            total += self.vector_store.add_documents(batch_rows, embeddings)
            print(f"Ingested food knowledge chunks {total}/{len(rows)}", flush=True)
        return total

    def search(self, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
        final_top_k = top_k or settings.KB_TOP_K
        recall_top_n = max(final_top_k, settings.RERANK_TOP_N)
        embedding = self.embedder.embed_query(query)
        results = self.vector_store.search(embedding, recall_top_n)
        if not self.reranker:
            return results[:final_top_k]
        try:
            return self.reranker.rerank(query, results, final_top_k)
        except Exception:
            logger.exception("DashScope rerank failed; using vector search ordering.")
            return results[:final_top_k]

    @staticmethod
    def format_context(results: list[dict[str, Any]]) -> str:
        if not results:
            return "未检索到相关饮食知识片段。"
        sections = []
        for index, item in enumerate(results, start=1):
            sections.append(
                "\n".join(
                    [
                        f"片段 {index}",
                        f"来源: {item.get('source', '')}#{item.get('chunk_index', '')}",
                        f"相似度: {item.get('score', '')}",
                        f"内容: {item.get('content', '')}",
                    ]
                )
            )
        return "\n\n".join(sections)

    def close(self) -> None:
        self.vector_store.close()
