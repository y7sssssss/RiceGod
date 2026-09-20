"""Opt-in real PostgreSQL checks, isolated fixtures, no external model calls.

Run with RICEGOD_TEST_POSTGRES=1. Only generated thread keys/user rows are removed.
"""

import asyncio
import json
import os
from uuid import uuid4
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from ricegod.application.agents import conversation, workflow
from ricegod.application.agents.state import QueryPlan, RecommendationGroup, TaskResult
from ricegod.application.chat.models import ChatRequest
from ricegod.application.chat.service import ChatService, _thread_key
from ricegod.application.memory.models import MemoryWriteResult
from ricegod.application.memory.service import MemoryService
from ricegod.application.memory.extractor import MemoryExtractor
from ricegod.config import settings
from ricegod.domain.recipe_ranking import RecipeRequest
from ricegod.domain.recipes import RecipeConstraints
from ricegod.infrastructure.checkpoint_store import open_checkpointer
from ricegod.infrastructure.memory_store import MemoryStore
from ricegod.infrastructure.recipe_graph_repository import RecipeGraphRepository
from ricegod.infrastructure.recipe_repository import RecipeRepository
from scripts.verification_cleanup import cleanup_verification_rows


pytestmark = pytest.mark.skipif(os.getenv("RICEGOD_TEST_POSTGRES") != "1", reason="Opt-in real PostgreSQL integration")


@pytest.fixture
def persisted_graph(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CHECKPOINT_BACKEND", "postgres")
    monkeypatch.setattr(settings, "LLM_API_KEY", None)
    rows = {name: {"name": name, "主食材": [[ingredient, "适量"]], "辅料": [], "口味": "清淡",
                   "工艺": method, "耗时": "十分钟", "类型": "家常菜", "做法": "1:准备。2:煮熟。"}
            for name, ingredient, method in [("鸡蛋小炒", "鸡蛋", "炒"), ("鸡蛋蒸羹", "鸡蛋", "蒸"),
                                              ("鸡蛋煎饼", "鸡蛋", "煎"), ("土豆小炒", "土豆", "炒"),
                                              ("土豆蒸泥", "土豆", "蒸") ]}
    path = tmp_path / "recipes.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    catalog, local = RecipeGraphRepository(data_path=path), RecipeRepository(str(path))
    monkeypatch.setattr(catalog, "query_ranked_candidates", lambda *args: [])
    monkeypatch.setattr(catalog, "query_by_cypher", lambda *args: [])
    user = "state-integration-" + uuid4().hex
    keys = set()

    def config(conversation="default", user_id=user):
        key = _thread_key(user_id, conversation)
        keys.add(key)
        return {"configurable": {"thread_id": key}}

    def graph(saver):
        return workflow.build_graph(conversation.general_query,
                                    conversation.additional_query, None,
                                    catalog, local, checkpointer=saver)

    yield user, config, graph
    catalog.client.close()
    cleanup_verification_rows([user, user + "-other"], keys)


def names(groups):
    return [[row.get("菜名") or row["name"] for row in group.records] for group in groups]


def test_state_types_order_and_continuous_selection_survive_new_graph(persisted_graph):
    _, config, build = persisted_graph
    cfg = config()
    with open_checkpointer() as saver:
        graph = build(saver)
        before = asyncio.run(graph.ainvoke({"messages": [HumanMessage(content="用鸡蛋推荐三道菜")]}, cfg))
        original_names = names(before["last_recommendations"])
        assert len(original_names[0]) == 3
        original_ids = [m.id for m in before["messages"]]

    # A new pool, saver, graph and service cannot reuse any in-memory checkpoint.
    with open_checkpointer() as saver:
        graph = build(saver)

        async def verify():
            restored = (await graph.aget_state(cfg)).values
            assert names(restored["last_recommendations"]) == original_names
            assert [m.id for m in restored["messages"]] == original_ids
            assert isinstance(restored["plan"], QueryPlan)
            assert isinstance(restored["plan"].constraints, RecipeConstraints)
            assert isinstance(restored["plan"].recipe_request, RecipeRequest)
            assert isinstance(restored["last_recommendations"][0], RecommendationGroup)
            assert all(isinstance(result, TaskResult) for result in restored["task_results"].values())
            for message, index in [("谢谢", None), ("第二道菜怎么做", 1), ("第三道菜怎么做", 2)]:
                result = await graph.ainvoke({"messages": [HumanMessage(content=message)]}, cfg)
                assert names(result["last_recommendations"]) == original_names
                if index is not None:
                    selected = [row["name"] for task in result["task_results"].values()
                                if task.route == "recipe_query" for row in task.records]
                    assert selected == [original_names[0][index]]
            assert len(result["messages"]) == 8
            # History readers may inspect the current state without a held DB cursor lock.
            count = 0
            async for snapshot in graph.aget_state_history(cfg, limit=2):
                assert snapshot.values
                assert (await asyncio.wait_for(graph.aget_state(cfg), 2)).values
                count += 1
            assert count == 2

        asyncio.run(verify())


def test_grouped_conditions_and_group_numbering_survive_restart(persisted_graph):
    _, config, build = persisted_graph
    cfg = config()
    with open_checkpointer() as saver:
        before = asyncio.run(build(saver).ainvoke({"messages": [HumanMessage(
            content="早餐用鸡蛋推荐两道菜，晚餐用土豆推荐两道菜。") ]}, cfg))
        assert [g.label for g in before["last_recommendations"]] == ["早餐", "晚餐"]
        original = names(before["last_recommendations"])
    with open_checkpointer() as saver:
        result = asyncio.run(build(saver).ainvoke({"messages": [HumanMessage(content="晚餐第二道菜怎么做")]}, cfg))
        rows = [row for task in result["task_results"].values() if task.route == "recipe_query" for row in task.records]
        assert [row["name"] for row in rows] == [original[1][1]]
        assert names(result["last_recommendations"]) == original
        assert result["plan"].tasks[0].recipe_request.detailed


def test_user_and_conversation_isolation_survive_restart(persisted_graph):
    user, config, build = persisted_graph
    first, other_user, other_conversation = config("shared"), config("shared", user + "-other"), config("other")
    with open_checkpointer() as saver:
        asyncio.run(build(saver).ainvoke({"messages": [HumanMessage(content="用鸡蛋推荐三道菜")]}, first))
    with open_checkpointer() as saver:
        graph = build(saver)

        async def verify():
            assert (await graph.aget_state(first)).values["last_recommendations"]
            for cfg in (other_user, other_conversation):
                assert not (await graph.aget_state(cfg)).values
                result = await graph.ainvoke({"messages": [HumanMessage(content="第二道菜怎么做")]}, cfg)
                assert not result["last_recommendations"] and not result["sources"]
                assert "还没有可选择的推荐列表" in result["answer"]

        asyncio.run(verify())


def test_confirmed_answer_and_message_history_survive_restart(persisted_graph):
    user, config, build = persisted_graph
    cfg = config()
    store = MemoryStore()

    class Memory:
        def recall(self, **kwargs):
            return []

        def build_memory_prompt(self, rows):
            return ""

        async def extract_and_store_result(self, **kwargs):
            current = store.get_recent_messages("default", 1, user_id=user)[0]
            record = store.add_memory(user_id=user, type_="disliked_ingredient", content="不吃香菜",
                embedding=[0.0] * settings.EMBEDDING_DIMENSION, source_message_id=current["id"], evidence="不吃香菜")
            return MemoryWriteResult(saved=[record])

    with open_checkpointer() as saver:
        service = ChatService(build(saver), store, Memory())
        response = asyncio.run(service.chat(ChatRequest(user_id=user, message="你好，请记住我不吃香菜")))
        assert response.memory_write.status == "saved"
        assert "已保存长期记忆：不吃香菜" in response.answer
    with open_checkpointer() as saver:
        graph = build(saver)
        restored = asyncio.run(graph.aget_state(cfg)).values
        assert restored["messages"][-1].content == restored["answer"] == response.answer
        assert store.get_messages("default", user_id=user)[-1].content == response.answer
        assert len(restored["messages"]) == 2

        class NoRecoveryStore:
            def get_recent_messages(self, *args, **kwargs):
                raise AssertionError("Persisted State must not be duplicated by raw history recovery")

            def add_message(self, **kwargs):
                return store.add_message(**kwargs)

        service = ChatService(graph, NoRecoveryStore())
        asyncio.run(service.chat(ChatRequest(user_id=user, message="谢谢")))
        assert len(asyncio.run(graph.aget_state(cfg)).values["messages"]) == 4


def test_chat_turn_idempotency_and_completion_survive_service_restart(persisted_graph):
    user, config, build = persisted_graph
    conversation_id = "idempotency"
    config(conversation_id)
    store = MemoryStore()
    request = ChatRequest(request_id="retry-" + uuid4().hex, user_id=user,
                          conversation_id=conversation_id, message="你好")

    with open_checkpointer() as saver:
        graph = build(saver)
        first = asyncio.run(ChatService(graph, store).chat(request))
        second = asyncio.run(ChatService(graph, store).chat(request))

    turn = store.begin_turn(conversation_id=conversation_id, user_id=user,
                            request_id=request.request_id, content=request.message)
    messages = store.get_messages(conversation_id, user_id=user)
    assert first.answer == second.answer
    assert turn["turn_status"] == "completed"
    assert turn["turn_response"]["answer"] == first.answer
    assert [message.role for message in messages] == ["user", "assistant"]


def operation_service(store):
    service = MemoryService.__new__(MemoryService)
    service.store, service.extractor = store, MemoryExtractor()
    service.embedder = SimpleNamespace(embed_query=lambda text: [0.0] * settings.EMBEDDING_DIMENSION)
    service.top_k, service.recent_messages, service.similarity_threshold = 5, 8, 0.9
    return service


def seed_memory(store, user, content, type_="dietary_preference"):
    source = store.add_message("default", user, "user", content)
    return store.add_memory(user_id=user, type_=type_, content=content,
        embedding=[0.0] * settings.EMBEDDING_DIMENSION, source_message_id=source.id, evidence=content)


def test_corrected_and_forgotten_memory_and_receipts_survive_restart(persisted_graph):
    user, config, build = persisted_graph
    cfg, store = config(), MemoryStore()
    original = seed_memory(store, user, "喜欢清淡")
    with open_checkpointer() as saver:
        response = asyncio.run(ChatService(build(saver), store, operation_service(store)).chat(
            ChatRequest(user_id=user, message="把清淡偏好改成喜欢麻辣")))
        assert response.memory_write.status == "updated"
        record = store.list_memories(user)[0]
        assert record.id == original.id and record.content == record.evidence == "喜欢麻辣"
        assert record.source_message_id != original.source_message_id
    with open_checkpointer() as saver:
        graph = build(saver)
        assert graph.get_state(cfg).values["answer"] == response.answer
        response = asyncio.run(ChatService(graph, store, operation_service(store)).chat(
            ChatRequest(user_id=user, message="忘掉麻辣偏好")))
        assert response.memory_write.deleted_ids == [original.id]
        assert store.list_memories(user) == [] and response.used_memories == []
    with open_checkpointer() as saver:
        state = build(saver).get_state(cfg).values
        assert state["answer"] == response.answer == store.get_messages("default", user_id=user)[-1].content
        assert state["messages"][-2].content == "忘掉麻辣偏好"
        assert len(state["messages"]) == 5


def test_forget_then_query_uses_updated_constraints_in_real_checkpoint(persisted_graph):
    user, config, build = persisted_graph
    cfg, store = config(), MemoryStore()
    seed_memory(store, user, "不吃鸡蛋", "disliked_ingredient")
    with open_checkpointer() as saver:
        graph = build(saver)
        response = asyncio.run(ChatService(graph, store, operation_service(store)).chat(ChatRequest(
            user_id=user, message="忘掉不吃鸡蛋的记忆，用鸡蛋推荐两道菜")))
        assert response.memory_write.status == "deleted"
        state = graph.get_state(cfg).values
        assert state["question"] == "用鸡蛋推荐两道菜"
        assert state["plan"].constraints.excluded_ingredients == []
        assert len(state["last_recommendations"][0].records) == 2
        assert state["messages"][-2].content == "忘掉不吃鸡蛋的记忆，用鸡蛋推荐两道菜"


def test_memory_compare_and_swap_and_default_delete_are_real_user_scoped(persisted_graph):
    user, _, _ = persisted_graph
    store = MemoryStore()
    record = seed_memory(store, user, "喜欢清淡")
    source = store.add_message("default", user, "user", "喜欢麻辣")
    assert store.update_memory(memory_id=record.id, user_id=user, content="喜欢麻辣",
        embedding=[0.0] * settings.EMBEDDING_DIMENSION, source_message_id=source.id, evidence="喜欢麻辣",
        expected_content="已经过时的内容") is None
    assert not store.delete_memory(record.id, user_id=user, expected_content="已经过时的内容")
    assert not store.delete_memory(record.id, user_id=user + "-other")
    assert store.list_memories(user)[0].content == "喜欢清淡"
    assert store.delete_memory(record.id, user_id=user)


def test_context_window_and_forget_boundary_survive_new_pool(persisted_graph, monkeypatch):
    from ricegod.application.agents.context import build_model_context
    from ricegod.application.agents.state import AgentState

    user, config, build = persisted_graph
    cfg, store = config(), MemoryStore()
    seed_memory(store, user, "不吃鸡蛋", "disliked_ingredient")
    monkeypatch.setattr(settings, "CONTEXT_RECENT_TURNS", 2)
    with open_checkpointer() as saver:
        graph = build(saver)
        service = ChatService(graph, store, operation_service(store))
        asyncio.run(service.chat(ChatRequest(user_id=user, message="你好，我不吃鸡蛋")))
        response = asyncio.run(service.chat(ChatRequest(user_id=user,
            message="忘掉不吃鸡蛋的记忆，用鸡蛋推荐两道菜")))
        assert response.memory_write.status == "deleted"
        state = graph.get_state(cfg).values
        marker, expected = state["context_history_after"], names(state["last_recommendations"])
        assert marker == state["messages"][-2].id
    with open_checkpointer() as saver:
        graph = build(saver)
        service = ChatService(graph, store, operation_service(store))

        async def run():
            for _ in range(8):
                await service.chat(ChatRequest(user_id=user, message="谢谢"))
            await service.chat(ChatRequest(user_id=user, message="第二道怎么做"))
            state = (await graph.aget_state(cfg)).values
            assert state["context_history_after"] == marker
            assert names(state["last_recommendations"]) == expected
            assert state["plan"].selected_recipe_name == expected[0][1]
            assert len(state["messages"]) == 23
            inputs = build_model_context(AgentState(**state), "answer")
            assert "不吃鸡蛋" not in str(inputs)
            assert "不吃鸡蛋" in str(state["messages"])
            assert sum(isinstance(m, AIMessage) for m in inputs) <= 2
        asyncio.run(run())
