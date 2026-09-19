"""Explicit in-memory fixtures: exercise business rules, not SQL or embeddings."""

from datetime import datetime, timezone
from types import SimpleNamespace

from ricegod.application.agents import conversation, router, workflow
from ricegod.application.memory.extractor import MemoryExtractor
from ricegod.application.memory.models import MemoryRecord, MemorySearchResult, MessageRecord
from ricegod.application.memory.service import MemoryService
from ricegod.bootstrap import Runtime
from ricegod.config import settings
from ricegod.infrastructure.recipe_graph_repository import RecipeGraphRepository
from ricegod.infrastructure.recipe_repository import RecipeRepository


class FixtureMemoryStore:
    def __init__(self):
        self.messages = []
        self.memories = []
        self.next_memory_id = 1

    def add_message(self, conversation_id, user_id, role, content):
        row = MessageRecord(id=len(self.messages) + 1, conversation_id=conversation_id, user_id=user_id,
                            role=role, content=content, created_at=datetime.now(timezone.utc))
        self.messages.append(row)
        return row

    def get_recent_messages(self, conversation_id, limit, *, user_id):
        return [row.model_dump() for row in self.get_messages(conversation_id, user_id=user_id)[-limit:]]

    def get_messages(self, conversation_id, *, user_id, limit=200):
        return [m for m in self.messages if m.user_id == user_id and m.conversation_id == conversation_id][-limit:]

    def get_message(self, message_id, *, user_id, conversation_id):
        return next((m for m in self.messages if m.id == message_id and m.user_id == user_id
                     and m.conversation_id == conversation_id), None)

    def list_memories(self, user_id):
        return [m for m in self.memories if m.user_id == user_id]

    def get_constraint_memories(self, user_id):
        return [m for m in self.list_memories(user_id) if m.type in ("allergy", "disliked_ingredient")]

    def search_memories(self, *, user_id, top_k, **kwargs):
        return [MemorySearchResult(**m.model_dump(), score=0) for m in self.list_memories(user_id)[:top_k]]

    def add_memory(self, *, user_id, type_, content, source_message_id, evidence, **kwargs):
        source = next(m for m in self.messages if m.id == source_message_id and m.user_id == user_id)
        if source.role != "user" or evidence != content or evidence not in source.content:
            raise ValueError("Invalid fixture evidence")
        now = datetime.now(timezone.utc)
        row = MemoryRecord(id=self.next_memory_id, user_id=user_id, type=type_, content=content,
                           source_message_id=source_message_id, evidence=evidence, created_at=now, updated_at=now)
        self.next_memory_id += 1
        self.memories.append(row)
        return row

    def update_memory(self, *, memory_id, user_id, content, source_message_id, evidence, expected_content, **kwargs):
        row = next((m for m in self.memories if m.id == memory_id and m.user_id == user_id
                    and m.content == expected_content), None)
        if row is None:
            return None
        new = row.model_copy(update={"content": content, "source_message_id": source_message_id,
                                    "evidence": evidence, "updated_at": datetime.now(timezone.utc)})
        self.memories[self.memories.index(row)] = new
        return new

    def delete_memory(self, memory_id, *, user_id, expected_content=None):
        row = next((m for m in self.memories if m.id == memory_id and m.user_id == user_id
                    and (expected_content is None or m.content == expected_content)), None)
        if row is None:
            return False
        self.memories.remove(row)
        return True


def create_offline_runtime():
    catalog, local = RecipeGraphRepository(), RecipeRepository()
    try:
        graph = workflow.build_graph(router.analyze_intent, conversation.general_query,
                                    conversation.additional_query, router._build_llm, catalog, local)
        store = FixtureMemoryStore()
        service = MemoryService.__new__(MemoryService)
        service.store, service.extractor = store, MemoryExtractor()
        service.embedder = SimpleNamespace(embed_query=lambda text: [0.0] * settings.EMBEDDING_DIMENSION)
        service.top_k = settings.MEMORY_TOP_K
        service.recent_messages = settings.MEMORY_RECENT_MESSAGES
        service.similarity_threshold = settings.MEMORY_SIMILARITY_THRESHOLD
        return Runtime(graph, catalog, local, store, service)
    except Exception:
        catalog.client.close()
        raise
