from ricegod.infrastructure.food_knowledge_repository import FoodKnowledgeRepository


class FakeEmbedder:
    def embed_documents(self, texts):
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, text):
        return [1.0, 0.0]


class FakeVectorStore:
    def __init__(self):
        self.rows = []
        self.searched_top_k = None

    def add_documents(self, rows, embeddings):
        self.rows.extend(rows)
        return len(rows)

    def search(self, embedding, top_k):
        self.searched_top_k = top_k
        return self.rows[:top_k]

    def clear(self):
        self.rows.clear()

    def close(self):
        pass


class FakeReranker:
    def rerank(self, query, rows, top_k):
        ranked = list(reversed(rows))
        for row in ranked:
            row["score"] = 0.99
        return ranked[:top_k]


class ScorePreservingReranker:
    def rerank(self, query, rows, top_k):
        return rows[:top_k]


def test_food_knowledge_repository_builds_rows_from_chunks():
    repository = FoodKnowledgeRepository(
        embedder=FakeEmbedder(),
        vector_store=FakeVectorStore(),
        reranker=FakeReranker(),
        chunk_size=20,
        chunk_overlap=0,
    )

    chunks = repository.split_text("辣椒传入中国。\n\n铁锅在宋朝逐渐普及。")
    rows = repository.build_rows(chunks, "data.txt")

    assert chunks
    assert rows[0]["id"] == "data.txt_1"
    assert rows[0]["source"] == "data.txt"
    assert rows[0]["chunk_index"] == 1
    assert rows[0]["content"]


def test_food_knowledge_repository_formats_search_context():
    context = FoodKnowledgeRepository.format_context(
        [
            {
                "id": "data.txt_1",
                "content": "辣椒在明朝传入中国，普遍食用更晚。",
                "score": 0.9,
                "source": "data.txt",
                "chunk_index": 1,
            }
        ]
    )

    assert "片段 1" in context
    assert "data.txt#1" in context
    assert "辣椒" in context


def test_food_knowledge_repository_formats_empty_context():
    assert FoodKnowledgeRepository.format_context([]) == "未检索到相关饮食知识片段。"


def test_food_knowledge_repository_reranks_vector_results(monkeypatch):
    monkeypatch.setattr("ricegod.infrastructure.food_knowledge_repository.settings.RERANK_TOP_N", 3)
    vector_store = FakeVectorStore()
    vector_store.rows = [
        {"id": "1", "content": "较弱相关", "score": 0.8, "source": "data.txt", "chunk_index": 1},
        {"id": "2", "content": "中等相关", "score": 0.7, "source": "data.txt", "chunk_index": 2},
        {"id": "3", "content": "最相关", "score": 0.6, "source": "data.txt", "chunk_index": 3},
    ]
    repository = FoodKnowledgeRepository(
        embedder=FakeEmbedder(),
        vector_store=vector_store,
        reranker=FakeReranker(),
    )

    results = repository.search("问题", top_k=2)

    assert vector_store.searched_top_k == 3
    assert [item["id"] for item in results] == ["3", "2"]
    assert all(item["score"] == 0.99 for item in results)


def test_food_knowledge_repository_filters_low_vector_scores(monkeypatch):
    monkeypatch.setattr("ricegod.infrastructure.food_knowledge_repository.settings.RERANK_TOP_N", 3)
    vector_store = FakeVectorStore()
    vector_store.rows = [
        {"id": "1", "content": "相关", "score": 0.8, "source": "data.txt", "chunk_index": 1},
        {"id": "2", "content": "低相似", "score": 0.2, "source": "data.txt", "chunk_index": 2},
    ]
    repository = FoodKnowledgeRepository(
        embedder=FakeEmbedder(),
        vector_store=vector_store,
        reranker=ScorePreservingReranker(),
    )

    results = repository.search("问题", top_k=2, vector_similarity_threshold=0.5,
                                rerank_relevance_threshold=0.0)

    assert [item["id"] for item in results] == ["1"]


def test_food_knowledge_repository_filters_low_rerank_scores(monkeypatch):
    monkeypatch.setattr("ricegod.infrastructure.food_knowledge_repository.settings.RERANK_TOP_N", 3)
    vector_store = FakeVectorStore()
    vector_store.rows = [
        {"id": "1", "content": "相关", "score": 0.9, "source": "data.txt", "chunk_index": 1},
        {"id": "2", "content": "干扰", "score": 0.8, "source": "data.txt", "chunk_index": 2},
    ]

    class Reranker:
        def rerank(self, query, rows, top_k):
            rows[0]["score"] = 0.85
            rows[1]["score"] = 0.1
            return rows

    repository = FoodKnowledgeRepository(
        embedder=FakeEmbedder(),
        vector_store=vector_store,
        reranker=Reranker(),
    )

    results = repository.search("问题", top_k=2, vector_similarity_threshold=0.0,
                                rerank_relevance_threshold=0.25)

    assert [item["id"] for item in results] == ["1"]
