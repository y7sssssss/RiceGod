import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ricegod.config import settings


class DashScopeRerankClient:
    """Rerank text chunks with DashScope text rerank API."""

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        endpoint: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.model = model or settings.RERANK_MODEL
        self.api_key = api_key or settings.RERANK_API_KEY or settings.EMBEDDING_API_KEY or settings.LLM_API_KEY
        self.endpoint = endpoint or settings.RERANK_ENDPOINT
        self.timeout = timeout if timeout is not None else settings.RERANK_TIMEOUT_SECONDS
        if not self.api_key:
            raise RuntimeError("RERANK_API_KEY, EMBEDDING_API_KEY, or LLM_API_KEY is not configured.")

    def rerank(self, query: str, rows: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
        if not rows:
            return []

        documents = [{"text": str(row.get("content", ""))} for row in rows]
        payload = {
            "model": self.model,
            "input": {
                "query": {"text": query},
                "documents": documents,
            },
            "parameters": {
                "return_documents": False,
                "top_n": min(top_k, len(rows)),
            },
        }
        request = Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as exc:
            raise RuntimeError(f"DashScope rerank request failed: {exc}") from exc

        ranked_rows: list[dict[str, Any]] = []
        for item in data.get("output", {}).get("results", []):
            index = item.get("index")
            if not isinstance(index, int) or index < 0 or index >= len(rows):
                continue
            row = dict(rows[index])
            row["vector_score"] = row.get("score")
            row["score"] = float(item.get("relevance_score", 0.0))
            ranked_rows.append(row)

        return ranked_rows or rows[:top_k]
