from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row

from ricegod.application.memory.models import (
    HIGH_PRIORITY_MEMORY_TYPES,
    CONSTRAINT_MEMORY_TYPES,
    MemoryRecord,
    MemorySearchResult,
    MemoryType,
    MessageRecord,
)
from ricegod.config import settings
from ricegod.application.memory.provenance import find_user_evidence, normalize_text

try:
    from pgvector.psycopg import register_vector
except Exception:  # pragma: no cover - optional dependency fallback
    register_vector = None


MEMORY_TYPES: tuple[MemoryType, ...] = (
    "dietary_preference",
    "allergy",
    "disliked_ingredient",
    "liked_cuisine",
    "cooking_constraint",
    "equipment",
    "household",
    "goal",
    "recipe_feedback",
    "todo",
)


class MemoryStore:
    """PostgreSQL store for raw messages and distilled long-term memories."""

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or settings.POSTGRES_DSN or (
            f"host={settings.POSTGRES_HOST} "
            f"port={settings.POSTGRES_PORT} "
            f"dbname={settings.POSTGRES_DB} "
            f"user={settings.POSTGRES_USER} "
            f"password={settings.POSTGRES_PASSWORD}"
        )
        self.init_schema()

    def _connect(self, *, register_pgvector: bool = True) -> psycopg.Connection:
        conn = psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row, connect_timeout=5)
        if register_pgvector and register_vector:
            register_vector(conn)
        return conn

    def init_schema(self) -> None:
        type_values = ", ".join(f"'{value}'" for value in MEMORY_TYPES)
        with self._connect(register_pgvector=False) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            if register_vector:
                register_vector(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id BIGSERIAL PRIMARY KEY,
                    conversation_id VARCHAR(128) NOT NULL,
                    user_id VARCHAR(128) NOT NULL,
                    role VARCHAR(16) NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
                    content TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
                ON messages (conversation_id, created_at)
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_user_conversation_created "
                         "ON messages (user_id, conversation_id, created_at)")
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS memories (
                    id BIGSERIAL PRIMARY KEY,
                    user_id VARCHAR(128) NOT NULL,
                    type VARCHAR(64) NOT NULL CHECK (type IN ({type_values})),
                    content TEXT NOT NULL,
                    embedding VECTOR NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            conn.execute("ALTER TABLE memories ADD COLUMN IF NOT EXISTS source_message_id "
                         "BIGINT REFERENCES messages(id) ON DELETE SET NULL")
            conn.execute("ALTER TABLE memories ADD COLUMN IF NOT EXISTS evidence TEXT NOT NULL DEFAULT ''")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_user_id ON memories (user_id)")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memories_type_user
                ON memories (type, user_id)
                """
            )

    def add_message(self, conversation_id: str, user_id: str, role: str, content: str) -> MessageRecord:
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO messages (conversation_id, user_id, role, content)
                VALUES (%s, %s, %s, %s)
                RETURNING id, conversation_id, user_id, role, content, created_at
                """,
                (conversation_id, user_id, role, content),
            ).fetchone()
        if not row:
            raise RuntimeError("Failed to insert message.")
        return MessageRecord.model_validate(row)

    def get_recent_messages(self, conversation_id: str, limit: int, *, user_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, conversation_id, user_id, role, content, created_at
                FROM messages
                WHERE conversation_id = %s AND user_id = %s
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (conversation_id, user_id, limit),
            ).fetchall()
        rows.reverse()
        return rows

    def get_message(self, message_id: int, *, user_id: str, conversation_id: str) -> MessageRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT id, conversation_id, user_id, role, content, created_at FROM messages "
                               "WHERE id = %s AND user_id = %s AND conversation_id = %s",
                               (message_id, user_id, conversation_id)).fetchone()
        return MessageRecord.model_validate(row) if row else None

    def get_messages(self, conversation_id: str, limit: int = 200, *, user_id: str) -> list[MessageRecord]:
        rows = self.get_recent_messages(conversation_id, limit, user_id=user_id)
        return [MessageRecord.model_validate(row) for row in rows]

    def find_similar_memory(
        self,
        *,
        user_id: str,
        embedding: list[float],
    ) -> MemorySearchResult | None:
        vector = _to_vector_literal(embedding)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    id,
                    user_id,
                    type,
                    content,
                    created_at,
                    updated_at,
                    source_message_id,
                    evidence,
                    1 - (embedding <=> %s::vector) AS score
                FROM memories
                WHERE user_id = %s
                ORDER BY embedding <=> %s::vector
                LIMIT 1
                """,
                (vector, user_id, vector),
            ).fetchone()
        if not row:
            return None
        return MemorySearchResult.model_validate(row)

    @staticmethod
    def _require_user_evidence(conn, *, user_id: str, source_message_id: int, evidence: str, content: str) -> str:
        row = conn.execute("SELECT id, role, content FROM messages WHERE id = %s AND user_id = %s",
                           (source_message_id, user_id)).fetchone()
        quote = find_user_evidence([row] if row else [], source_message_id, evidence)
        if quote is None or normalize_text(content) != quote:
            raise ValueError("Memory content requires matching evidence from this user's message.")
        return quote

    def add_memory(self, *, user_id: str, type_: MemoryType, content: str, embedding: list[float],
                   source_message_id: int, evidence: str) -> MemoryRecord:
        vector = _to_vector_literal(embedding)
        with self._connect() as conn:
            quote = self._require_user_evidence(conn, user_id=user_id, source_message_id=source_message_id,
                                               evidence=evidence, content=content)
            row = conn.execute(
                """
                INSERT INTO memories (user_id, type, content, embedding, source_message_id, evidence)
                VALUES (%s, %s, %s, %s::vector, %s, %s)
                RETURNING id, user_id, type, content, created_at, updated_at, source_message_id, evidence
                """,
                (user_id, type_, quote, vector, source_message_id, quote),
            ).fetchone()
        if not row:
            raise RuntimeError("Failed to insert memory.")
        return MemoryRecord.model_validate(row)

    def update_memory(
        self,
        *,
        memory_id: int,
        user_id: str,
        content: str,
        embedding: list[float],
        source_message_id: int,
        evidence: str,
        expected_content: str | None = None,
    ) -> MemoryRecord | None:
        vector = _to_vector_literal(embedding)
        with self._connect() as conn:
            quote = self._require_user_evidence(conn, user_id=user_id, source_message_id=source_message_id,
                                               evidence=evidence, content=content)
            row = conn.execute(
                """
                UPDATE memories
                SET content = %s,
                    embedding = %s::vector,
                    source_message_id = %s,
                    evidence = %s,
                    updated_at = NOW()
                WHERE id = %s
                  AND user_id = %s
                  AND (%s::text IS NULL OR content = %s)
                RETURNING id, user_id, type, content, created_at, updated_at, source_message_id, evidence
                """,
                (quote, vector, source_message_id, quote, memory_id, user_id, expected_content, expected_content),
            ).fetchone()
        if not row:
            return None
        return MemoryRecord.model_validate(row)

    def search_memories(self, *, user_id: str, query_embedding: list[float], top_k: int) -> list[MemorySearchResult]:
        vector = _to_vector_literal(query_embedding)
        semantic_limit = max(top_k * 4, 12)
        with self._connect() as conn:
            semantic_rows = conn.execute(
                """
                SELECT
                    id,
                    user_id,
                    type,
                    content,
                    created_at,
                    updated_at,
                    source_message_id,
                    evidence,
                    1 - (embedding <=> %s::vector) AS score
                FROM memories
                WHERE user_id = %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (vector, user_id, vector, semantic_limit),
            ).fetchall()
            priority_rows = conn.execute(
                """
                SELECT
                    id,
                    user_id,
                    type,
                    content,
                    created_at,
                    updated_at,
                    source_message_id,
                    evidence,
                    0.0::double precision AS score
                FROM memories
                WHERE user_id = %s
                  AND type = ANY(%s)
                ORDER BY updated_at DESC
                LIMIT 4
                """,
                (user_id, list(HIGH_PRIORITY_MEMORY_TYPES)),
            ).fetchall()

        by_id: dict[int, MemorySearchResult] = {}
        for row in semantic_rows:
            item = MemorySearchResult.model_validate(row)
            by_id[item.id] = item
        for row in priority_rows:
            item = MemorySearchResult.model_validate(row)
            current = by_id.get(item.id)
            if not current:
                by_id[item.id] = item

        def boosted_score(item: MemorySearchResult) -> float:
            boost = 0.08 if item.type in HIGH_PRIORITY_MEMORY_TYPES else 0.0
            return float(item.score) + boost

        ranked = sorted(by_id.values(), key=boosted_score, reverse=True)
        return ranked[:top_k]

    def list_memories(self, user_id: str) -> list[MemoryRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, user_id, type, content, created_at, updated_at, source_message_id, evidence
                FROM memories
                WHERE user_id = %s
                ORDER BY updated_at DESC, id DESC
                """,
                (user_id,),
            ).fetchall()
        return [MemoryRecord.model_validate(row) for row in rows]

    def get_constraint_memories(self, user_id: str) -> list[MemoryRecord]:
        """All explicit exclusions; these must not compete for semantic top-k slots."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, user_id, type, content, created_at, updated_at, source_message_id, evidence FROM memories "
                "WHERE user_id = %s AND type = ANY(%s) ORDER BY updated_at DESC, id DESC",
                (user_id, list(CONSTRAINT_MEMORY_TYPES)),
            ).fetchall()
        return [MemoryRecord.model_validate(row) for row in rows]

    def delete_memory(self, memory_id: int, *, user_id: str, expected_content: str | None = None) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "DELETE FROM memories WHERE id = %s AND user_id = %s "
                "AND (%s::text IS NULL OR content = %s) RETURNING id",
                (memory_id, user_id, expected_content, expected_content),
            ).fetchone()
        return bool(row)


def _to_vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{float(v):.8f}" for v in values) + "]"
