from typing import Any
from uuid import uuid4

from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility

from ricegod.config import settings


class MilvusVectorStore:
    """Minimal Milvus vector store for food knowledge chunks."""

    def __init__(
        self,
        collection_name: str | None = None,
        host: str | None = None,
        port: int | None = None,
        dimension: int | None = None,
        index_type: str | None = None,
        metric_type: str | None = None,
    ) -> None:
        self.collection_name = collection_name or settings.MILVUS_COLLECTION
        self.host = host or settings.MILVUS_HOST
        self.port = port or settings.MILVUS_PORT
        self.dimension = dimension or settings.EMBEDDING_DIMENSION
        self.index_type = index_type or settings.MILVUS_INDEX_TYPE
        self.metric_type = metric_type or settings.MILVUS_METRIC_TYPE
        self.collection: Collection | None = None
        self.alias = "ricegod_" + uuid4().hex
        try:
            self._connect()
        except Exception:
            connections.disconnect(self.alias)
            raise

    def _connect(self) -> None:
        connections.connect(alias=self.alias, host=self.host, port=str(self.port), timeout=10)
        if utility.has_collection(self.collection_name, using=self.alias):
            self.collection = Collection(self.collection_name, using=self.alias)
        else:
            self.collection = self._create_collection()
        self.collection.load()

    def _create_collection(self) -> Collection:
        fields = [
            FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=256),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=self.dimension),
            FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=65535),
            FieldSchema(name="source", dtype=DataType.VARCHAR, max_length=256),
            FieldSchema(name="chunk_index", dtype=DataType.INT64),
        ]
        schema = CollectionSchema(fields=fields, description="RiceGod food culture knowledge chunks")
        collection = Collection(name=self.collection_name, schema=schema, using=self.alias)
        collection.create_index(
            field_name="embedding",
            index_params={
                "index_type": self.index_type,
                "metric_type": self.metric_type,
                "params": {"nlist": 128} if self.index_type == "IVF_FLAT" else {},
            },
        )
        return collection

    def add_documents(
        self,
        rows: list[dict[str, Any]],
        embeddings: list[list[float]],
    ) -> int:
        if not rows:
            return 0
        if len(rows) != len(embeddings):
            raise ValueError("Rows and embeddings must have the same length.")
        assert self.collection is not None
        entities = [
            [row["id"] for row in rows],
            embeddings,
            [row["content"] for row in rows],
            [row["source"] for row in rows],
            [int(row["chunk_index"]) for row in rows],
        ]
        self.collection.insert(entities)
        self.collection.flush()
        self.collection.load()
        return len(rows)

    def search(self, embedding: list[float], top_k: int) -> list[dict[str, Any]]:
        assert self.collection is not None
        results = self.collection.search(
            data=[embedding],
            anns_field="embedding",
            param={"metric_type": self.metric_type, "params": {"nprobe": 10}},
            limit=top_k,
            output_fields=["id", "content", "source", "chunk_index"],
        )
        formatted: list[dict[str, Any]] = []
        for hits in results:
            for hit in hits:
                formatted.append(
                    {
                        "id": hit.entity.get("id"),
                        "content": hit.entity.get("content"),
                        "score": float(hit.score),
                        "source": hit.entity.get("source"),
                        "chunk_index": hit.entity.get("chunk_index"),
                    }
                )
        return formatted

    def clear(self) -> None:
        if utility.has_collection(self.collection_name, using=self.alias):
            utility.drop_collection(self.collection_name, using=self.alias)
        self.collection = self._create_collection()
        self.collection.load()

    def count(self) -> int:
        assert self.collection is not None
        return int(self.collection.num_entities)

    def close(self) -> None:
        connections.disconnect(self.alias)
