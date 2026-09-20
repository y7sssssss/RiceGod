import asyncio
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage
from psycopg.conninfo import conninfo_to_dict

from ricegod.application.agents import knowledge_retrieval, recipe_retrieval
from ricegod.application.agents.state import PlannedTask, QueryPlan, TaskResult
from ricegod.application.chat.models import ChatRequest
from ricegod.application.chat.service import ChatService
from ricegod.config import settings
from ricegod.infrastructure.memory_store import MemoryStore, _memory_search_result


def test_generated_dsn_quotes_special_characters(monkeypatch):
    monkeypatch.setattr(MemoryStore, "init_schema", lambda self: None)
    monkeypatch.setattr(settings, "POSTGRES_DSN", None)
    monkeypatch.setattr(settings, "POSTGRES_PASSWORD", "pa ss'\\word")

    store = MemoryStore()

    assert conninfo_to_dict(store.dsn)["password"] == "pa ss'\\word"


def test_non_finite_vector_score_is_normalized_for_json_persistence():
    now = datetime.now(timezone.utc)
    result = _memory_search_result({
        "id": 1,
        "user_id": "u",
        "type": "dietary_preference",
        "content": "喜欢清淡",
        "created_at": now,
        "updated_at": now,
        "source_message_id": None,
        "evidence": "",
        "score": float("nan"),
    })

    assert result.score == 0.0


def test_template_recipe_query_does_not_require_llm_key(monkeypatch, local):
    monkeypatch.setattr(settings, "LLM_API_KEY", None)
    calls = []

    class Repository:
        def build_predefined_cypher(self, question):
            return SimpleNamespace(cypher="MATCH template", params={"name": "番茄炒蛋"})

        def query_by_cypher(self, cypher, params):
            calls.append((cypher, params))
            return [{"菜名": "番茄炒蛋"}]

        @staticmethod
        def format_records(rows):
            return "番茄炒蛋"

    task = PlannedTask(id="recipe", route="recipe_query", question="番茄炒蛋怎么做")
    result = asyncio.run(recipe_retrieval.retrieve_recipe(
        task, QueryPlan(), Repository(), local, model_factory=None))

    assert calls == [("MATCH template", {"name": "番茄炒蛋"})]
    assert result.status == "ok"


def test_knowledge_retrieval_uses_embedding_key_without_llm_key(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", None)
    monkeypatch.setattr(settings, "EMBEDDING_API_KEY", "embedding-key")

    class Repository:
        def search(self, query, top_k):
            return [{"content": "川菜历史", "source": "kb.md", "chunk_index": 1}]

        def close(self):
            pass

        @staticmethod
        def format_context(rows):
            return rows[0]["content"]

    monkeypatch.setattr(knowledge_retrieval, "FoodKnowledgeRepository", Repository)
    task = PlannedTask(id="knowledge", route="food_knowledge", question="川菜历史")

    result = asyncio.run(knowledge_retrieval.retrieve_knowledge(task, []))

    assert result.status == "ok"
    assert result.sources == ["kb.md#1"]


def test_idempotent_turn_is_shared_across_service_instances():
    class Lease:
        def __init__(self, lock):
            self.lock = lock

        def release(self):
            self.lock.release()

    class Store:
        def __init__(self):
            self.lock = threading.Lock()
            self.turn = None
            self.messages = []

        def acquire_conversation_lock(self, **kwargs):
            self.lock.acquire()
            return Lease(self.lock)

        def begin_turn(self, *, conversation_id, user_id, request_id, content):
            if self.turn is None:
                self.turn = {"id": 1, "conversation_id": conversation_id, "user_id": user_id,
                             "role": "user", "content": content, "request_id": request_id,
                             "turn_status": "processing", "turn_response": None}
                self.messages.append(dict(self.turn))
            return dict(self.turn)

        def complete_turn(self, message_id, response, *, assistant_content):
            self.turn["turn_status"] = "completed"
            self.turn["turn_response"] = response
            self.messages.append({"id": 2, "role": "assistant", "content": assistant_content})

        def fail_turn(self, message_id, error_code="chat_execution_failed"):
            self.turn["turn_status"] = "failed"

        def get_recent_messages(self, conversation_id, limit, *, user_id):
            return list(self.messages)

    class Graph:
        def __init__(self):
            self.calls = 0
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def aget_state(self, config):
            return SimpleNamespace(values={})

        async def ainvoke(self, data, config):
            self.calls += 1
            self.entered.set()
            await self.release.wait()
            return {"messages": [AIMessage(content="ok")],
                    "router": {"type": ["general_query"], "logic": "test"}}

    async def scenario():
        store = Store()
        graph = Graph()
        first_service = ChatService(graph, store)
        second_service = ChatService(graph, store)
        request = ChatRequest(request_id="same-request", user_id="u", conversation_id="c", message="hello")

        first = asyncio.create_task(first_service.chat(request))
        await asyncio.wait_for(graph.entered.wait(), 1)
        second = asyncio.create_task(second_service.chat(request))
        await asyncio.sleep(0.02)
        graph.release.set()
        first_response, second_response = await asyncio.wait_for(asyncio.gather(first, second), 2)

        assert graph.calls == 1
        assert first_response.answer == second_response.answer == "ok"
        assert [row["role"] for row in store.messages] == ["user", "assistant"]
        assert store.turn["turn_status"] == "completed"

    asyncio.run(scenario())
