import pytest
from fastapi.testclient import TestClient

from ricegod.main import create_app, health
from ricegod.bootstrap import Runtime
from ricegod.application.agents import conversation, workflow
from ricegod.config import settings


@pytest.fixture
def client(monkeypatch, catalog, local):
    monkeypatch.setattr(settings, "LLM_API_KEY", None)
    graph = workflow.build_graph(conversation.general_query,
                                 conversation.additional_query, None, catalog, local)
    with TestClient(create_app(lambda: Runtime(graph, catalog, local))) as client:
        yield client


def test_health_endpoint():
    assert health()["status"] == "ok"


def test_demo_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "RiceGod" in response.text
    assert "GraphRAG" in response.text


def test_chat_endpoint_returns_route_without_llm_key(client):
    response = client.post("/chat", json={"message": "hello", "thread_id": "test-chat"})
    assert response.status_code == 200
    assert response.json()["route"] == "general_query"
    assert response.json()["answer"]


def test_chat_endpoint_routes_recipe_query_without_llm_key(client):
    response = client.post("/chat", json={"message": "番茄炒蛋怎么做", "thread_id": "test-recipe"})
    assert response.status_code == 200
    assert response.json()["route"] == "recipe_query"
    assert response.json()["answer"]


def test_chat_endpoint_routes_food_knowledge_without_llm_key(client):
    response = client.post("/chat", json={"message": "川菜有什么历史特点", "thread_id": "test-culture"})
    assert response.status_code == 200
    assert response.json()["route"] == "food_knowledge"
    assert response.json()["answer"]


def test_chat_endpoint_routes_multi_task_query_without_llm_key(client):
    response = client.post("/chat", json={"message": "我不吃辣，推荐一道川菜并介绍一下它的来历", "thread_id": "test-multi-task"})
    assert response.status_code == 200
    assert response.json()["route"] == "recipe_query+food_knowledge"
    assert response.json()["answer"]
