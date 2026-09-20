import subprocess
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from ricegod import bootstrap
from ricegod.main import create_app


def test_importing_api_and_planner_does_not_construct_resources():
    code = """
from unittest.mock import patch
with patch('ricegod.infrastructure.recipe_graph_repository.RecipeGraphRepository', side_effect=AssertionError('catalog constructed')), \
     patch('ricegod.infrastructure.recipe_repository.RecipeRepository', side_effect=AssertionError('local constructed')), \
     patch('ricegod.infrastructure.memory_store.MemoryStore', side_effect=AssertionError('store constructed')), \
     patch('ricegod.application.agents.workflow.build_graph', side_effect=AssertionError('graph constructed')):
    import ricegod.main as api
    from ricegod.application.agents import planner
    assert not hasattr(api.app.state, 'runtime')
    assert not hasattr(planner, 'graph')
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_lifespan_constructs_one_runtime_per_app_and_closes_on_failure():
    events = []

    def factory():
        runtime = SimpleNamespace(close=lambda: events.append("close"))
        events.append(runtime)
        return runtime

    app = create_app(factory)
    assert events == []
    with pytest.raises(RuntimeError, match="test request failure"):
        with TestClient(app) as client:
            assert app.state.runtime is events[0]
            assert client.get("/health").status_code == 200
            assert client.get("/health").status_code == 200
            assert len(events) == 1
            raise RuntimeError("test request failure")
    assert events[-1] == "close"
    assert app.state.runtime is None
    with TestClient(app):
        assert app.state.runtime is events[2]
        assert events[0] is not events[2]
    assert events[-1] == "close"


def test_memory_startup_failure_keeps_chat_available(monkeypatch):
    from ricegod.config import settings
    monkeypatch.setattr(settings, "CHECKPOINT_BACKEND", "memory")
    closed = []
    catalog = SimpleNamespace(client=SimpleNamespace(close=lambda: closed.append(True)))

    class Graph:
        async def aget_state(self, config):
            return SimpleNamespace(values={})

        async def ainvoke(self, data, config):
            return {"messages": [AIMessage(content="可用菜谱证据")],
                    "router": {"type": ["recipe_query"], "logic": "test"}, "sources": ["fixture"]}

    def unavailable():
        raise RuntimeError("PostgreSQL unavailable")

    monkeypatch.setattr(bootstrap, "RecipeGraphRepository", lambda: catalog)
    monkeypatch.setattr(bootstrap, "RecipeRepository", lambda: object())
    monkeypatch.setattr(bootstrap, "build_graph", lambda *args, **kwargs: Graph())
    monkeypatch.setattr(bootstrap, "MemoryStore", unavailable)
    app = create_app(bootstrap.create_runtime)
    with TestClient(app) as client:
        assert app.state.runtime.memory_store is None
        response = client.post("/chat", json={"message": "推荐菜谱"})
        assert response.status_code == 200
        assert response.json()["answer"] == "可用菜谱证据"
        assert response.json()["memory_write"]["status"] == "unavailable"
        assert client.get("/memories", params={"user_id": "u"}).status_code == 503
        assert client.get("/messages", params={"user_id": "u", "conversation_id": "c"}).status_code == 503
        assert client.delete("/memories/1", params={"user_id": "u"}).status_code == 503
    assert closed == [True]


def test_failed_graph_construction_closes_created_catalog(monkeypatch):
    from ricegod.config import settings
    monkeypatch.setattr(settings, "CHECKPOINT_BACKEND", "memory")
    closed = []
    catalog = SimpleNamespace(client=SimpleNamespace(close=lambda: closed.append(True)))

    def fail(*args, **kwargs):
        raise RuntimeError("Graph build failed")

    monkeypatch.setattr(bootstrap, "RecipeGraphRepository", lambda: catalog)
    monkeypatch.setattr(bootstrap, "RecipeRepository", lambda: object())
    monkeypatch.setattr(bootstrap, "build_graph", fail)
    with pytest.raises(RuntimeError, match="Graph build failed"):
        bootstrap.create_runtime()
    assert closed == [True]


def test_chat_execution_error_is_mapped_to_http_500():
    from ricegod.application.chat.service import ChatService

    class Graph:
        async def aget_state(self, config):
            return SimpleNamespace(values={})

        async def ainvoke(self, data, config):
            raise RuntimeError("Graph invocation failed")

    runtime = SimpleNamespace(chat_service=ChatService(Graph()), close=lambda: None)
    with TestClient(create_app(lambda: runtime)) as client:
        response = client.post("/chat", json={"message": "hello"})
        assert response.status_code == 500
        assert response.json()["detail"] == "Chat service is temporarily unavailable."
        assert "Graph invocation failed" not in response.text
