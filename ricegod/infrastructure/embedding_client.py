from collections.abc import Sequence

from openai import OpenAI

from ricegod.config import settings


class OpenAICompatibleEmbeddingClient:
    """Embedding client for OpenAI-compatible APIs such as DashScope."""

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        batch_size: int = 10,
    ) -> None:
        self.model = model or settings.EMBEDDING_MODEL
        self.api_key = api_key or settings.EMBEDDING_API_KEY or settings.LLM_API_KEY
        self.base_url = base_url or settings.EMBEDDING_BASE_URL or settings.LLM_BASE_URL
        self.batch_size = batch_size
        if not self.api_key:
            raise RuntimeError("EMBEDDING_API_KEY or LLM_API_KEY is not configured.")
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=20.0, max_retries=1)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        embeddings: list[list[float]] = []
        clean_texts = [(text or " ").strip() or " " for text in texts]
        for start in range(0, len(clean_texts), self.batch_size):
            batch = clean_texts[start : start + self.batch_size]
            response = self.client.embeddings.create(model=self.model, input=batch)
            embeddings.extend([list(item.embedding) for item in response.data])
        if len(embeddings) != len(clean_texts):
            raise RuntimeError("Embedding API returned unexpected number of vectors.")
        return embeddings

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]
