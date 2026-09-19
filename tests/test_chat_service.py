import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ricegod.application.chat.models import ChatRequest
from ricegod.application.chat.service import ChatService, _thread_key, _records_to_graph_messages
from ricegod.application.memory.models import MemoryWriteResult
from ricegod.config import settings

class _Snapshot:
    def __init__(self, values):
        self.values = values


def test_same_conversation_lock_covers_memory_confirmation_and_history():
    async def scenario():
        events = []
        writing = asyncio.Event()
        release = asyncio.Event()

        class Graph:
            async def aget_state(self, config):
                return SimpleNamespace(values={"messages": [HumanMessage(content="old")]})

            async def ainvoke(self, data, config):
                events.append(("generate", data["messages"][-1].content))
                return {"messages": [AIMessage(content="ok")], "router": {"type": ["general_query"]}}

        class Store:
            def add_message(self, **kw):
                events.append((kw["role"], kw["content"]))

        class Memory:
            def recall(self, **kw):
                return []

            def build_memory_prompt(self, rows):
                return ""

            async def extract_and_store_result(self, **kw):
                events.append(("memory", "start"))
                if not writing.is_set():
                    writing.set()
                    await release.wait()
                return MemoryWriteResult()

        service = ChatService(Graph(), Store(), Memory())
        first = asyncio.create_task(service.chat(ChatRequest(user_id="u", conversation_id="c", message="first")))
        await asyncio.wait_for(writing.wait(), 1)
        second = asyncio.create_task(service.chat(ChatRequest(user_id="u", conversation_id="c", message="second")))
        # Wait until the second caller reaches the turn boundary, without releasing the first write.
        await asyncio.sleep(0)
        assert ("user", "second") not in events and ("generate", "second") not in events
        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), 2)
        assert events.index(("assistant", "ok")) < events.index(("user", "second"))

    asyncio.run(scenario())


@pytest.mark.parametrize("other_user,other_conversation", [("u", "other"), ("other", "c")])
def test_different_users_or_conversations_can_run_concurrently(other_user, other_conversation):
    async def scenario():
        entered = set()
        barrier = asyncio.Event()

        class Graph:
            async def aget_state(self, config):
                return SimpleNamespace(values={})

            async def ainvoke(self, data, config):
                entered.add(config["configurable"]["thread_id"])
                if len(entered) == 2:
                    barrier.set()
                await asyncio.wait_for(barrier.wait(), 1)
                return {"messages": [AIMessage(content="ok")], "router": {"type": ["general_query"]}}

        service = ChatService(Graph())
        await asyncio.gather(service.chat(ChatRequest(user_id="u", conversation_id="c", message="first")),
            service.chat(ChatRequest(user_id=other_user, conversation_id=other_conversation, message="second")))
        assert len(entered) == 2

    asyncio.run(scenario())


def test_chat_recovers_recent_history_when_checkpoint_empty(monkeypatch):
    captured: dict[str, object] = {}
    call_order: list[str] = []

    class FakeGraph:
        async def aget_state(self, config):
            captured["state_config"] = config
            return _Snapshot(values={})

        async def ainvoke(self, input_value, config=None):
            captured["input"] = input_value
            captured["config"] = config
            return {
                "messages": [AIMessage(content="ok")],
                "router": {"type": "general_query", "logic": "test"},
                "sources": [],
            }

    class FakeMemoryStore:
        def get_recent_messages(self, conversation_id, limit, *, user_id):
            captured["history_user_id"] = user_id
            call_order.append("get_recent")
            return [
                {"role": "user", "content": "history-q1"},
                {"role": "assistant", "content": "history-a1"},
                {"role": "system", "content": "should-be-skipped"},
                {"role": "assistant", "content": "   "},
            ]

        def add_message(self, conversation_id, user_id, role, content):
            call_order.append(f"add:{role}:{content}")
            return None

    class FakeMemoryService:
        def recall(self, *, user_id, query, top_k=None):
            return []

        def build_memory_prompt(self, memories):
            return "MEM_CTX"

        async def extract_and_store_result(self, *, user_id, conversation_id):
            return MemoryWriteResult()

    service = ChatService(FakeGraph(), FakeMemoryStore(), FakeMemoryService())
    monkeypatch.setattr(settings, "RECENT_CONTEXT_MESSAGES", 8)

    request = ChatRequest(user_id="u1", conversation_id="c1", message="current-q")
    response = asyncio.run(service.chat(request))

    assert response.answer == "ok"
    assert call_order[0] == "get_recent"
    assert call_order[1] == "add:user:current-q"
    assert captured["history_user_id"] == "u1"
    assert captured["config"]["configurable"]["thread_id"] == _thread_key("u1", "c1")

    graph_input = captured["input"]
    assert graph_input["memory_context"] == "MEM_CTX"
    messages = graph_input["messages"]
    assert [type(msg) for msg in messages] == [HumanMessage, AIMessage, HumanMessage]
    assert [msg.content for msg in messages] == ["history-q1", "history-a1", "current-q"]
    assert sum(msg.content == "current-q" for msg in messages) == 1
    assert not any(isinstance(msg, SystemMessage) for msg in messages)


def test_chat_skips_db_recovery_when_checkpoint_has_messages(monkeypatch):
    captured: dict[str, object] = {}

    class FakeGraph:
        async def aget_state(self, config):
            return _Snapshot(values={"messages": [HumanMessage(content="existing")]})

        async def ainvoke(self, input_value, config=None):
            captured["input"] = input_value
            captured["config"] = config
            return {
                "messages": [AIMessage(content="ok")],
                "router": {"type": "general_query", "logic": "test"},
                "sources": [],
            }

    class FakeMemoryStore:
        def get_recent_messages(self, conversation_id, limit, *, user_id):
            raise AssertionError("Should not recover DB history when checkpoint has messages.")

        def add_message(self, conversation_id, user_id, role, content):
            return None

    class FakeMemoryService:
        def recall(self, *, user_id, query, top_k=None):
            return []

        def build_memory_prompt(self, memories):
            return "MEM_CTX"

        async def extract_and_store_result(self, *, user_id, conversation_id):
            return MemoryWriteResult()

    service = ChatService(FakeGraph(), FakeMemoryStore(), FakeMemoryService())

    request = ChatRequest(user_id="u1", conversation_id="c1", message="current-q")
    response = asyncio.run(service.chat(request))

    assert response.answer == "ok"
    graph_input = captured["input"]
    assert graph_input["memory_context"] == "MEM_CTX"
    messages = graph_input["messages"]
    assert len(messages) == 1
    assert isinstance(messages[0], HumanMessage)
    assert messages[0].content == "current-q"


def test_records_to_graph_messages_skips_system_and_empty_content():
    records = [
        {"role": "system", "content": "system-ignore"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "  "},
    ]
    messages = _records_to_graph_messages(records)
    assert len(messages) == 2
    assert isinstance(messages[0], HumanMessage)
    assert isinstance(messages[1], AIMessage)
    assert messages[0].content == "hello"
    assert messages[1].content == "world"


def test_chat_skips_recovery_if_checkpoint_probe_fails(monkeypatch):
    captured: dict[str, object] = {}

    class FakeGraph:
        async def aget_state(self, config):
            raise RuntimeError("state unavailable")

        async def ainvoke(self, input_value, config=None):
            captured["input"] = input_value
            return {
                "messages": [AIMessage(content="ok")],
                "router": {"type": "general_query", "logic": "test"},
                "sources": [],
            }

    class FakeMemoryStore:
        def get_recent_messages(self, conversation_id, limit, *, user_id):
            raise AssertionError("Should skip DB recovery when checkpoint probing fails.")

        def add_message(self, conversation_id, user_id, role, content):
            return None

    class FakeMemoryService:
        def recall(self, *, user_id, query, top_k=None):
            return []

        def build_memory_prompt(self, memories):
            return ""

        async def extract_and_store_result(self, *, user_id, conversation_id):
            return MemoryWriteResult()

    service = ChatService(FakeGraph(), FakeMemoryStore(), FakeMemoryService())

    response = asyncio.run(service.chat(ChatRequest(user_id="u1", conversation_id="c1", message="current-q")))
    assert response.answer == "ok"
    messages = captured["input"]["messages"]
    assert len(messages) == 1
    assert isinstance(messages[0], HumanMessage)
