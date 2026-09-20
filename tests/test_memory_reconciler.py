from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from ricegod.application.memory.models import MemoryExtractionResult, MemoryItem, MemoryRecord, MemorySearchResult
from ricegod.application.memory.service import MemoryService


def _item(*, type: str, content: str, evidence: str | None = None, source_message_id: int = 1) -> MemoryItem:
    return MemoryItem(type=type, content=content, evidence=evidence or content, source_message_id=source_message_id)


def _record(memory_id: int, content: str, type_: str = "dietary_preference") -> MemoryRecord:
    now = datetime.now(timezone.utc)
    return MemoryRecord(
        id=memory_id,
        user_id="u1",
        type=type_,
        content=content,
        created_at=now,
        updated_at=now,
    )


def _search(memory_id: int, content: str, score: float = 0.95, type_: str = "dietary_preference") -> MemorySearchResult:
    now = datetime.now(timezone.utc)
    return MemorySearchResult(
        id=memory_id,
        user_id="u1",
        type=type_,
        content=content,
        created_at=now,
        updated_at=now,
        score=score,
    )


class FakeExtractor:
    def __init__(self, memories: list[MemoryItem]) -> None:
        self.memories = memories

    async def extract(self, recent_messages):
        return MemoryExtractionResult(memories=self.memories)


class FakeEmbedder:
    def __init__(self) -> None:
        self.calls = 0

    def embed_query(self, text: str) -> list[float]:
        self.calls += 1
        return [float(len(text) % 10), 1.0]


class FakeStore:
    def __init__(self, related: list[MemorySearchResult] | None = None) -> None:
        self.related = related or []
        self.calls: list[tuple] = []

    def get_recent_messages(self, conversation_id, limit, *, user_id):
        return self.messages

    def get_message(self, message_id, *, user_id, conversation_id):
        row = next((row for row in self.messages if row["id"] == message_id
                    and row.get("user_id") == user_id and row.get("conversation_id") == conversation_id), None)
        return SimpleNamespace(**row) if row else None

    def search_memories(self, *, user_id, query_embedding, top_k):
        self.calls.append(("search", user_id, top_k))
        return self.related

    def list_memories(self, user_id):
        return [MemoryRecord.model_validate(row.model_dump()) for row in self.related if row.user_id == user_id]

    def add_memory(self, *, user_id, type_, content, embedding, source_message_id, evidence):
        self.calls.append(("add", user_id, type_, content))
        return _record(100, content, type_).model_copy(update={"source_message_id": source_message_id, "evidence": evidence})

    def update_memory(self, *, memory_id, user_id, content, embedding, source_message_id, evidence):
        self.calls.append(("update", memory_id, user_id, content))
        return _record(memory_id, content).model_copy(update={"source_message_id": source_message_id, "evidence": evidence})

    def delete_memory(self, memory_id, user_id=None, expected_content=None):
        self.calls.append(("delete", memory_id, user_id))
        return True


class FakeModel:
    def __init__(self, responses: list[list[dict]]) -> None:
        self.responses = responses
        self.calls = 0

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        index = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return SimpleNamespace(tool_calls=self.responses[index])


def _service(store: FakeStore, extractor: FakeExtractor, model: FakeModel | None = None) -> MemoryService:
    service = MemoryService.__new__(MemoryService)
    service.store = store
    service.extractor = extractor
    service.embedder = FakeEmbedder()
    service.similarity_threshold = 0.9
    service.top_k = 5
    service.recent_messages = 8
    store.messages = [{"id": item.source_message_id, "role": "user", "content": item.evidence,
                       "user_id": "u1", "conversation_id": "c1"} for item in extractor.memories]
    if model:
        service._build_reconcile_model = lambda: model
    return service


def test_reconciler_add_tool(monkeypatch):
    monkeypatch.setattr("ricegod.application.memory.service.settings.LLM_API_KEY", "test-key")
    store = FakeStore()
    model = FakeModel([[{"name": "add_memory", "args": {"type": "liked_cuisine", "content": "用户喜欢川菜"}}]])
    service = _service(store, FakeExtractor([_item(type="liked_cuisine", content="用户喜欢川菜")]), model)

    changed = asyncio.run(service.extract_and_store(user_id="u1", conversation_id="c1"))

    assert [call[0] for call in store.calls] == ["search", "add"]
    assert changed[0].content == "用户喜欢川菜"


def test_ordinary_reconciler_cannot_update_even_with_allowed_id(monkeypatch):
    monkeypatch.setattr("ricegod.application.memory.service.settings.LLM_API_KEY", "test-key")
    store = FakeStore(related=[_search(7, "用户偏好清淡饮食")])
    model = FakeModel([[{"name": "update_memory", "args": {"memory_id": 7, "content": "用户偏好清淡少油饮食"}}]])
    service = _service(store, FakeExtractor([_item(type="dietary_preference", content="用户偏好清淡少油饮食")]), model)

    changed = asyncio.run(service.extract_and_store(user_id="u1", conversation_id="c1"))

    assert not any(call[0] == "update" for call in store.calls)
    assert changed == []


def test_reconciler_update_tool_rejects_unknown_id(monkeypatch):
    monkeypatch.setattr("ricegod.application.memory.service.settings.LLM_API_KEY", "test-key")
    store = FakeStore(related=[_search(7, "用户偏好清淡饮食")])
    model = FakeModel([[{"name": "update_memory", "args": {"memory_id": 99, "content": "用户偏好清淡少油饮食"}}]])
    service = _service(store, FakeExtractor([_item(type="dietary_preference", content="用户偏好清淡少油饮食")]), model)

    changed = asyncio.run(service.extract_and_store(user_id="u1", conversation_id="c1"))

    assert not any(call[0] == "update" for call in store.calls)
    assert changed == []


def test_ordinary_reconciler_cannot_delete_even_with_allowed_id(monkeypatch):
    monkeypatch.setattr("ricegod.application.memory.service.settings.LLM_API_KEY", "test-key")
    store = FakeStore(related=[_search(9, "用户在减脂", type_="goal")])
    model = FakeModel([[{"name": "delete_memory", "args": {"memory_id": 9, "reason": "用户要求忘记"}}]])
    service = _service(store, FakeExtractor([_item(type="goal", content="用户不再减脂")]), model)

    changed = asyncio.run(service.extract_and_store(user_id="u1", conversation_id="c1"))

    assert not any(call[0] == "delete" for call in store.calls)
    assert changed == []


def test_reconciler_delete_tool_rejects_unknown_id(monkeypatch):
    monkeypatch.setattr("ricegod.application.memory.service.settings.LLM_API_KEY", "test-key")
    store = FakeStore(related=[_search(9, "用户在减脂", type_="goal")])
    model = FakeModel([[{"name": "delete_memory", "args": {"memory_id": 99}}]])
    service = _service(store, FakeExtractor([_item(type="goal", content="用户不再减脂")]), model)

    changed = asyncio.run(service.extract_and_store(user_id="u1", conversation_id="c1"))

    assert not any(call[0] == "delete" for call in store.calls)
    assert changed == []


def test_reconciler_no_op_does_not_write(monkeypatch):
    monkeypatch.setattr("ricegod.application.memory.service.settings.LLM_API_KEY", "test-key")
    store = FakeStore(related=[_search(1, "用户不吃香菜", type_="disliked_ingredient")])
    model = FakeModel([[{"name": "no_op", "args": {"reason": "重复"}}]])
    service = _service(store, FakeExtractor([_item(type="disliked_ingredient", content="用户不吃香菜")]), model)

    changed = asyncio.run(service.extract_and_store(user_id="u1", conversation_id="c1"))

    assert [call[0] for call in store.calls] == ["search"]
    assert changed == []


def test_reconciler_retries_once_then_uses_tool(monkeypatch):
    monkeypatch.setattr("ricegod.application.memory.service.settings.LLM_API_KEY", "test-key")
    store = FakeStore()
    model = FakeModel([[], [{"name": "add_memory", "args": {"type": "liked_cuisine", "content": "用户喜欢粤菜"}}]])
    service = _service(store, FakeExtractor([_item(type="liked_cuisine", content="用户喜欢粤菜")]), model)

    changed = asyncio.run(service.extract_and_store(user_id="u1", conversation_id="c1"))

    assert model.calls == 2
    assert any(call[0] == "add" for call in store.calls)
    assert changed


def test_reconciler_fallback_without_llm_adds_when_not_similar(monkeypatch):
    monkeypatch.setattr("ricegod.application.memory.service.settings.LLM_API_KEY", None)
    store = FakeStore(related=[_search(1, "用户喜欢川菜", score=0.4, type_="liked_cuisine")])
    service = _service(store, FakeExtractor([_item(type="liked_cuisine", content="用户喜欢粤菜")]))

    changed = asyncio.run(service.extract_and_store(user_id="u1", conversation_id="c1"))

    assert any(call[0] == "add" for call in store.calls)
    assert service.embedder.calls == 1
    assert changed


def test_reconciler_fallback_without_llm_skips_when_similar(monkeypatch):
    monkeypatch.setattr("ricegod.application.memory.service.settings.LLM_API_KEY", None)
    store = FakeStore(related=[_search(1, "用户喜欢川菜", score=0.95, type_="liked_cuisine")])
    service = _service(store, FakeExtractor([_item(type="liked_cuisine", content="用户喜欢川菜")]))

    changed = asyncio.run(service.extract_and_store(user_id="u1", conversation_id="c1"))

    assert not any(call[0] == "add" for call in store.calls)
    assert changed == []


def test_reconciler_add_tool_rejects_sensitive_content(monkeypatch):
    monkeypatch.setattr("ricegod.application.memory.service.settings.LLM_API_KEY", "test-key")
    store = FakeStore()
    model = FakeModel([[{"name": "add_memory", "args": {"type": "todo", "content": "api_key is sk-1234567890123456"}}]])
    service = _service(store, FakeExtractor([_item(type="todo", content="用户要记住安全事项")]), model)

    changed = asyncio.run(service.extract_and_store(user_id="u1", conversation_id="c1"))

    assert not any(call[0] == "add" for call in store.calls)
    assert changed == []


def test_reconciler_cannot_merge_exclusion_into_preference(monkeypatch):
    monkeypatch.setattr("ricegod.application.memory.service.settings.LLM_API_KEY", "test-key")
    store = FakeStore(related=[_search(7, "不能吃辣", type_="dietary_preference")])
    model = FakeModel([[{"name": "update_memory", "args": {"memory_id": 7, "content": "不能吃辣，也不吃香菜"}}]])
    service = _service(store, FakeExtractor([_item(type="disliked_ingredient", content="不吃香菜")]), model)

    changed = asyncio.run(service.extract_and_store(user_id="u1", conversation_id="c1"))

    assert not any(call[0] == "update" for call in store.calls)
    assert changed == []


def test_reconciler_add_preserves_extracted_exclusion_type(monkeypatch):
    monkeypatch.setattr("ricegod.application.memory.service.settings.LLM_API_KEY", "test-key")
    store = FakeStore()
    model = FakeModel([[{"name": "add_memory", "args": {"type": "dietary_preference", "content": "不吃香菜"}}]])
    service = _service(store, FakeExtractor([_item(type="disliked_ingredient", content="不吃香菜")]), model)

    changed = asyncio.run(service.extract_and_store(user_id="u1", conversation_id="c1"))

    assert changed[0].type == "disliked_ingredient"
    assert ("add", "u1", "disliked_ingredient", "不吃香菜") in store.calls


def test_fallback_does_not_drop_exclusion_similar_to_different_type(monkeypatch):
    monkeypatch.setattr("ricegod.application.memory.service.settings.LLM_API_KEY", None)
    store = FakeStore(related=[_search(7, "不能吃辣", score=0.99, type_="dietary_preference")])
    service = _service(store, FakeExtractor([_item(type="disliked_ingredient", content="不吃香菜")]))

    changed = asyncio.run(service.extract_and_store(user_id="u1", conversation_id="c1"))

    assert changed[0].type == "disliked_ingredient"


def test_reconciler_add_cannot_expand_candidate_into_other_facts(monkeypatch):
    monkeypatch.setattr("ricegod.application.memory.service.settings.LLM_API_KEY", "test-key")
    store = FakeStore()
    model = FakeModel([[{"name": "add_memory", "args": {"type": "disliked_ingredient", "content": "不吃香菜，也不能吃辣"}}]])
    service = _service(store, FakeExtractor([_item(type="disliked_ingredient", content="不吃香菜")]), model)

    changed = asyncio.run(service.extract_and_store(user_id="u1", conversation_id="c1"))

    assert changed[0].content == "不吃香菜"
    assert ("add", "u1", "disliked_ingredient", "不吃香菜") in store.calls


def test_reconciler_update_cannot_invent_assistant_flavor(monkeypatch):
    monkeypatch.setattr("ricegod.application.memory.service.settings.LLM_API_KEY", "test-key")
    store = FakeStore(related=[_search(7, "不能吃辣")])
    model = FakeModel([[{"name": "update_memory", "args": {"memory_id": 7, "content": "喜欢葱香，不能吃辣"}}]])
    service = _service(store, FakeExtractor([_item(type="dietary_preference", content="不能吃辣")]), model)
    store.messages.append({"id": 2, "role": "assistant", "content": "葱香面饼适合你"})

    changed = asyncio.run(service.extract_and_store(user_id="u1", conversation_id="c1"))

    assert not any(call[0] == "update" for call in store.calls)
    assert changed == []


def test_service_revalidates_a_custom_extractor_and_never_embeds_assistant_facts(monkeypatch):
    monkeypatch.setattr("ricegod.application.memory.service.settings.LLM_API_KEY", None)
    store = FakeStore()
    service = _service(store, FakeExtractor([_item(type="dietary_preference", content="喜欢葱香", source_message_id=2)]))
    store.messages = [{"id": 1, "role": "user", "content": "用鸡蛋推荐一道家常菜"},
                      {"id": 2, "role": "assistant", "content": "喜欢葱香"}]
    service.embedder = SimpleNamespace(embed_query=lambda _: (_ for _ in ()).throw(AssertionError("must not embed")))

    assert asyncio.run(service.extract_and_store(user_id="u1", conversation_id="c1")) == []
    assert store.calls == []


def test_service_rejects_source_from_another_user_or_conversation(monkeypatch):
    monkeypatch.setattr("ricegod.application.memory.service.settings.LLM_API_KEY", None)
    for foreign_scope in [{"user_id": "u2"}, {"conversation_id": "c2"}]:
        store = FakeStore()
        service = _service(store, FakeExtractor([_item(type="dietary_preference", content="不能吃辣")]))
        store.messages[0].update(foreign_scope)
        assert asyncio.run(service.extract_and_store(user_id="u1", conversation_id="c1")) == []
        assert store.calls == []


def test_receipt_reports_actual_add_failure(monkeypatch):
    monkeypatch.setattr("ricegod.application.memory.service.settings.LLM_API_KEY", "test-key")
    store = FakeStore()
    def failed(**kwargs):
        raise RuntimeError("database write failed")
    store.add_memory = failed
    service = _service(store, FakeExtractor([_item(type="disliked_ingredient", content="不吃香菜")]),
        FakeModel([[{"name": "add_memory", "args": {"type": "disliked_ingredient", "content": "不吃香菜"}}]]))
    receipt = asyncio.run(service.extract_and_store_result(user_id="u1", conversation_id="c1"))
    assert receipt.status == "failed"
    assert receipt.saved == [] and receipt.failed_count == 1


def test_receipt_preserves_success_when_second_write_fails(monkeypatch):
    monkeypatch.setattr("ricegod.application.memory.service.settings.LLM_API_KEY", None)
    store = FakeStore()
    add = store.add_memory
    def partial(**kwargs):
        if kwargs["content"] == "不能吃辣":
            raise RuntimeError("second write failed")
        return add(**kwargs)
    store.add_memory = partial
    service = _service(store, FakeExtractor([
        _item(type="disliked_ingredient", content="不吃香菜"),
        _item(type="dietary_preference", content="不能吃辣")]))
    store.messages = [{"id": 1, "role": "user", "content": "不吃香菜，也不能吃辣",
                       "user_id": "u1", "conversation_id": "c1"}]
    receipt = asyncio.run(service.extract_and_store_result(user_id="u1", conversation_id="c1"))
    assert receipt.status == "partial"
    assert [record.content for record in receipt.saved] == ["不吃香菜"]
    assert receipt.failed_count == 1


def test_no_op_only_confirms_an_exact_stored_duplicate(monkeypatch):
    monkeypatch.setattr("ricegod.application.memory.service.settings.LLM_API_KEY", "test-key")
    for stored_content, expected in [("不吃香菜", "unchanged"), ("不吃香葱", "skipped")]:
        store = FakeStore(related=[_search(7, stored_content, type_="disliked_ingredient")])
        service = _service(store, FakeExtractor([_item(type="disliked_ingredient", content="不吃香菜")]),
                           FakeModel([[{"name": "no_op", "args": {"reason": "duplicate"}}]]))
        receipt = asyncio.run(service.extract_and_store_result(user_id="u1", conversation_id="c1"))
        assert receipt.status == expected
        assert bool(receipt.unchanged) == (expected == "unchanged")


def test_receipt_reports_deleted_ids_only_after_store_success(monkeypatch):
    monkeypatch.setattr("ricegod.application.memory.service.settings.LLM_API_KEY", "test-key")
    store = FakeStore(related=[_search(7, "喜欢川菜", type_="liked_cuisine")])
    service = _service(store, FakeExtractor([_item(type="liked_cuisine", content="忘记我喜欢川菜")]),
        FakeModel([[{"name": "delete_memory", "args": {"memory_id": 7}}]]))
    receipt = asyncio.run(service.extract_and_store_result(user_id="u1", conversation_id="c1"))
    assert receipt.status == "deleted" and receipt.deleted_ids == [7]


def test_missing_embedder_reports_unavailable():
    service = _service(FakeStore(), FakeExtractor([]))
    service.embedder = None
    receipt = asyncio.run(service.extract_and_store_result(user_id="u1", conversation_id="c1"))
    assert receipt.status == "unavailable" and receipt.saved == []
