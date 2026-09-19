import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage

import ricegod.main as main_module
from ricegod.application.agents import router, conversation, workflow
from ricegod.application.chat.service import _thread_key
from ricegod.application.memory.models import MemoryRecord, MemorySearchResult
from ricegod.application.memory.service import MemoryService
from ricegod.infrastructure.memory_store import MemoryStore


def _record(id_, type_):
    now = datetime.now(timezone.utc)
    return MemoryRecord(id=id_, user_id="u1", type=type_, content=f"restriction {id_}",
                        created_at=now, updated_at=now)


def test_all_exclusions_are_recalled_even_when_top_k_is_one():
    restrictions = [_record(id_, "allergy") for id_ in range(1, 9)]
    ordinary = MemorySearchResult(**_record(100, "goal").model_dump(), score=0.99)
    service = MemoryService.__new__(MemoryService)
    service.top_k = 1
    service.embedder = SimpleNamespace(embed_query=lambda query: [1.0])
    service.store = SimpleNamespace(search_memories=lambda **kwargs: [ordinary],
                                   get_constraint_memories=lambda user_id: restrictions)
    recalled = service.recall(user_id="u1", query="推荐晚餐")
    assert {item.id for item in recalled} == {*range(1, 9), 100}
    assert all(item.score == 0 for item in recalled if item.type == "allergy")


def test_nonsemantic_fallback_also_keeps_all_exclusions():
    restrictions = [_record(id_, "disliked_ingredient") for id_ in range(1, 9)]
    service = MemoryService.__new__(MemoryService)
    service.top_k = 1
    service.embedder = None
    service.store = SimpleNamespace(list_memories=lambda user_id: [_record(100, "goal")],
                                   get_constraint_memories=lambda user_id: restrictions)
    assert len(service.recall(user_id="u1", query="推荐晚餐")) == 9


def test_failed_embedding_recall_keeps_stored_memories_and_all_exclusions():
    restrictions = [_record(id_, "disliked_ingredient") for id_ in range(1, 9)]
    calls = []

    def unavailable(query):
        raise RuntimeError("embedding permission denied")

    def stored(user_id):
        calls.append(user_id)
        return [_record(100, "goal")]

    service = MemoryService.__new__(MemoryService)
    service.top_k = 1
    service.embedder = SimpleNamespace(embed_query=unavailable)
    service.store = SimpleNamespace(list_memories=stored, get_constraint_memories=lambda user_id: restrictions)

    recalled = service.recall(user_id="u1", query="推荐晚餐")

    assert calls == ["u1"]
    assert {item.id for item in recalled} == {*range(1, 9), 100}
    assert all(item.score == 0 for item in recalled)


def test_message_query_and_deletion_are_always_user_scoped():
    queries = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, sql, params):
            queries.append((sql, params))
            return SimpleNamespace(fetchall=lambda: [], fetchone=lambda: None)

    store = MemoryStore.__new__(MemoryStore)
    store._connect = lambda: Connection()
    store.get_recent_messages("shared", 8, user_id="u1")
    store.delete_memory(123, user_id="u2")
    assert "user_id = %s" in queries[0][0]
    assert queries[0][1] == ("shared", "u1", 8)
    assert "user_id = %s" in queries[1][0]
    assert queries[1][1] == (123, "u2", None, None)


def test_same_conversation_id_does_not_share_checkpoints_between_users(monkeypatch, catalog, local):
    monkeypatch.setattr(router.settings, "LLM_API_KEY", None)

    graph = workflow.build_graph(router.analyze_intent, conversation.general_query, conversation.additional_query, router._build_llm, catalog, local)

    async def scenario():
        first = {"configurable": {"thread_id": _thread_key("u1", "shared-isolation-test")}}
        second = {"configurable": {"thread_id": _thread_key("u2", "shared-isolation-test")}}
        await graph.ainvoke({"messages": [HumanMessage(content="你好，我是用户一")]}, first)
        snapshot = graph.get_state(second)
        assert not snapshot.values
        assert graph.get_state(first).values["messages"][0].content == "你好，我是用户一"

    asyncio.run(scenario())


def test_management_api_requires_user_id():
    runtime = SimpleNamespace(memory_store=object(), close=lambda: None)
    with TestClient(main_module.create_app(lambda: runtime)) as client:
        assert client.get("/messages", params={"conversation_id": "shared"}).status_code == 422
        assert client.delete("/memories/1").status_code == 422
