import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from ricegod.application.agents import conversation, workflow
from ricegod.application.agents.context import build_model_context
from ricegod.application.agents.state import AgentState
from ricegod.application.chat.models import ChatRequest
from ricegod.application.chat.service import ChatService, _thread_key
from ricegod.application.memory.models import MemoryExtractionResult, MemoryItem, MemoryRecord, MemorySearchResult, MessageRecord
from ricegod.application.memory.operations import MemoryCommand, apply_memory_command, parse_memory_command
from ricegod.application.memory.policy import extraction_skip_reason
from ricegod.application.memory.service import MemoryService
from ricegod.config import settings
from ricegod.infrastructure.recipe_graph_repository import RecipeGraphRepository
from ricegod.infrastructure.recipe_repository import RecipeRepository


def memory(id_, content, type_="dietary_preference", user="u"):
    now = datetime.now(timezone.utc)
    return MemoryRecord(id=id_, user_id=user, type=type_, content=content, created_at=now, updated_at=now)


class Store:
    def __init__(self, records=()):
        self.records = list(records)
        self.messages = []
        self.writes = []
        self.fail = False
        self.stale = False

    def add_message(self, conversation_id, user_id, role, content):
        row = MessageRecord(id=len(self.messages) + 1, conversation_id=conversation_id, user_id=user_id,
                            role=role, content=content, created_at=datetime.now(timezone.utc))
        self.messages.append(row)
        return row

    def get_recent_messages(self, conversation_id, limit, *, user_id):
        return [row.model_dump() for row in self.messages
                if row.user_id == user_id and row.conversation_id == conversation_id][-limit:]

    def get_message(self, message_id, *, user_id, conversation_id):
        return next((row for row in self.messages if row.id == message_id and row.user_id == user_id
                     and row.conversation_id == conversation_id), None)

    def list_memories(self, user_id):
        return [row for row in self.records if row.user_id == user_id]

    def search_memories(self, *, user_id, **kwargs):
        return [MemorySearchResult(**row.model_dump(), score=0) for row in self.list_memories(user_id)]

    def get_constraint_memories(self, user_id):
        return [row for row in self.list_memories(user_id) if row.type in {"allergy", "disliked_ingredient"}]

    def delete_memory(self, id_, *, user_id, expected_content=None):
        if self.fail:
            raise RuntimeError("Write failed")
        if self.stale:
            return False
        rows = [row for row in self.records if row.id == id_ and row.user_id == user_id
                and row.content == expected_content]
        for row in rows:
            self.records.remove(row)
            self.writes.append(("delete", row.id))
        return bool(rows)

    def add_memory(self, *, user_id, type_, content, source_message_id, evidence, **kwargs):
        assert content == evidence and content in self.messages[source_message_id - 1].content
        id_ = max([row.id for row in self.records] + [id_ for _, id_ in self.writes] + [0]) + 1
        row = memory(id_, content, type_, user_id).model_copy(update={
            "source_message_id": source_message_id, "evidence": evidence})
        self.records.append(row)
        self.writes.append(("add", id_))
        return row

    def update_memory(self, *, memory_id, user_id, content, source_message_id, evidence, expected_content, **kwargs):
        if self.fail:
            raise RuntimeError("Write failed")
        if self.stale:
            return None
        row = next(row for row in self.records if row.id == memory_id and row.user_id == user_id
                   and row.content == expected_content)
        assert evidence == content and content in self.messages[source_message_id - 1].content
        updated = row.model_copy(update={"content": content, "source_message_id": source_message_id, "evidence": evidence})
        self.records[self.records.index(row)] = updated
        self.writes.append(("update", row.id))
        return updated


class Extractor:
    def __init__(self):
        self.inputs = []

    async def extract(self, messages):
        self.inputs.append(messages)
        return MemoryExtractionResult()


def memory_service(store):
    service = MemoryService.__new__(MemoryService)
    service.store, service.extractor = store, Extractor()
    service.embedder = SimpleNamespace(embed_query=lambda text: [0.0, 1.0])
    service.top_k, service.recent_messages, service.similarity_threshold = 5, 8, 0.9
    return service


@pytest.fixture
def chat(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", None)
    rows = {name: {"name": name, "主食材": [["鸡蛋", "适量"], ["香菜", "适量"]], "辅料": [],
                   "口味": flavor, "工艺": "炒", "耗时": "十分钟", "类型": "家常菜", "做法": "1:准备。2:煮熟。"}
            for name, flavor in [("清淡鸡蛋", "清淡"), ("麻辣鸡蛋", "麻辣"), ("清淡蛋饼", "清淡")]}
    path = tmp_path / "recipes.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    catalog, local = RecipeGraphRepository(data_path=path), RecipeRepository(str(path))
    monkeypatch.setattr(catalog, "query_ranked_candidates", lambda *args: [])
    monkeypatch.setattr(catalog, "query_by_cypher", lambda *args: [])
    graph = workflow.build_graph(conversation.general_query,
                                conversation.additional_query, None, catalog, local)
    store = Store()
    memories = memory_service(store)
    yield ChatService(graph, store, memories), store, memories
    catalog.client.close()


def send(service, text):
    return asyncio.run(service.chat(ChatRequest(user_id="u", conversation_id="c", message=text)))


@pytest.mark.parametrize("text", ["谢谢！", "你好", "再见", "第二道菜怎么做", "展开第三道", "晚餐第二道怎么做", "第二组的第三道怎么做"])
def test_pure_messages_skip_extractor_but_keep_recall_and_history(chat, text):
    service, store, memories = chat
    store.records = [memory(1, "不吃香菜", "disliked_ingredient")]
    response = send(service, text)
    assert memories.extractor.inputs == []
    assert response.memory_write.extraction_status == "skipped"
    assert [row.id for row in response.used_memories] == [1]
    assert [row.content for row in store.messages] == [text, response.answer]


@pytest.mark.parametrize("text", ["谢谢，不过我不吃香菜", "第二道很好吃，以后多推荐这种", "今天想吃清淡的",
                                 "我没有烤箱", "你好，我喜欢清淡", "别忘了我喜欢清淡", "如何删除长期记忆？"])
def test_mixed_and_unknown_messages_still_call_extractor(chat, text):
    service, _, memories = chat
    send(service, text)
    assert len(memories.extractor.inputs) == 1


def test_correction_changes_one_row_before_recall_without_extraction(chat):
    service, store, memories = chat
    store.records = [memory(1, "喜欢清淡"), memory(2, "不吃香菜", "disliked_ingredient")]
    response = send(service, "把我的口味偏好从清淡改成喜欢麻辣")
    assert response.memory_write.status == "updated"
    assert [row.content for row in store.records] == ["喜欢麻辣", "不吃香菜"]
    assert [row.content for row in response.used_memories] == ["不吃香菜", "喜欢麻辣"]
    assert store.records[0].source_message_id == 1
    assert memories.extractor.inputs == []
    snapshot = service.graph.get_state({"configurable": {"thread_id": _thread_key("u", "c")}}).values
    assert snapshot["answer"] == snapshot["messages"][-1].content == response.answer == store.messages[-1].content
    assert len(snapshot["messages"]) == 2
    assert snapshot["context_history_after"] == snapshot["messages"][-2].id


def test_forget_then_recipe_request_does_not_reapply_quoted_old_restriction(chat):
    service, store, memories = chat
    store.records = [memory(1, "不吃香菜", "disliked_ingredient")]
    response = send(service, "忘掉不吃香菜的记忆，推荐一道含香菜的菜")
    assert response.memory_write.deleted_ids == [1]
    assert response.used_memories == [] and store.records == []
    cfg = {"configurable": {"thread_id": _thread_key("u", "c")}}
    state = service.graph.get_state(cfg).values
    assert state["plan"].constraints.excluded_ingredients == []
    assert state["plan"].constraints.required_ingredients == ["香菜"]
    assert any(task.records for task in state["task_results"].values())
    assert state["messages"][0].content == "忘掉不吃香菜的记忆，推荐一道含香菜的菜"
    assert state["context_history_after"] == state["messages"][0].id
    assert memories.extractor.inputs[0][0]["content"] == "推荐一道含香菜的菜"


def test_applied_forget_blocks_old_model_history_and_failed_forget_preserves_boundary(chat):
    service, store, _ = chat
    store.records = [memory(1, "不吃香菜", "disliked_ingredient")]
    send(service, "你好，我不吃香菜")
    send(service, "忘掉不吃香菜的记忆")
    cfg = {"configurable": {"thread_id": _thread_key("u", "c")}}
    marker = service.graph.get_state(cfg).values["context_history_after"]
    send(service, "忘掉不存在的偏好")
    send(service, "你好")
    state = service.graph.get_state(cfg).values
    inputs = build_model_context(AgentState(**state), "conversation")
    assert "不吃香菜" not in str(inputs)
    assert "不吃香菜" in str(state["messages"])
    assert state["context_history_after"] == marker and len(state["messages"]) == 8


@pytest.mark.parametrize("text,status", [("忘掉清淡偏好", "needs_clarification"),
    ("忘掉全部记忆", "needs_clarification"), ("忘掉这条记忆", "needs_clarification"),
    ("忘掉葱香偏好", "not_found"), ("把清淡偏好改成今天想吃麻辣", "rejected")])
def test_unresolved_operations_do_not_mutate_or_extract(chat, text, status):
    service, store, memories = chat
    store.records = [memory(1, "喜欢清淡"), memory(2, "偏好清淡少油")]
    response = send(service, text)
    assert response.memory_write.status == status
    assert len(store.records) == 2 and store.writes == [] and memories.extractor.inputs == []
    assert "已删除" not in response.answer and "已更新" not in response.answer


@pytest.mark.parametrize("text", ["忘掉清淡偏好", "把清淡偏好改成喜欢麻辣"])
@pytest.mark.parametrize("failure", ["fail", "stale"])
def test_write_failure_and_changed_target_never_claim_success(chat, text, failure):
    service, store, _ = chat
    store.records = [memory(1, "喜欢清淡")]
    setattr(store, failure, True)
    response = send(service, text)
    assert response.memory_write.status == "failed"
    assert store.records[0].content == "喜欢清淡"
    assert "已删除" not in response.answer and "已更新" not in response.answer


def test_operation_never_uses_foreign_user_rows(chat):
    service, store, _ = chat
    store.records = [memory(1, "喜欢清淡", user="someone-else")]
    response = send(service, "忘掉清淡偏好")
    assert response.memory_write.status == "not_found" and store.writes == []


def test_revocation_removes_only_positive_preference_not_infers_new_one(chat):
    service, store, _ = chat
    store.records = [memory(1, "喜欢清淡"), memory(2, "不喜欢清淡"), memory(3, "清淡过敏", "allergy")]
    response = send(service, "我不喜欢清淡了")
    assert response.memory_write.deleted_ids == [1]
    assert {row.id for row in store.records} == {2, 3}


def test_memory_operation_preserves_recommendations_and_next_turn_resets_question(chat):
    service, store, _ = chat
    send(service, "用鸡蛋推荐三道菜")
    cfg = {"configurable": {"thread_id": _thread_key("u", "c")}}
    original = service.graph.get_state(cfg).values["last_recommendations"]
    store.records = [memory(1, "喜欢清淡")]
    send(service, "忘掉清淡偏好")
    assert service.graph.get_state(cfg).values["last_recommendations"] == original
    response = send(service, "第二道怎么做")
    state = service.graph.get_state(cfg).values
    assert state["plan"].selected_recipe_name == original[0].records[1]["name"]
    assert response.memory_write.extraction_skip_reason == "pure_recipe_selection"


def test_forged_operation_cannot_execute_against_logged_evidence():
    store = Store([memory(1, "喜欢清淡")])
    message = store.add_message("c", "u", "user", "你好").model_dump()
    receipt = asyncio.run(apply_memory_command(memory_service(store), user_id="u", message=message,
                                                command=MemoryCommand("delete", "清淡")))
    assert receipt.status == "rejected" and store.writes == []


def extracting_constraint(memories):
    async def extract(messages):
        memories.extractor.inputs.append(messages)
        return MemoryExtractionResult(memories=[MemoryItem(type="disliked_ingredient", content="不吃香菜",
            evidence="不吃香菜", source_message_id=messages[0]["id"])])
    memories.extractor.extract = extract


def test_mixed_greeting_still_saves_verified_new_constraint(chat):
    service, store, memories = chat
    extracting_constraint(memories)
    response = send(service, "谢谢，不过我不吃香菜")
    assert response.memory_write.status == "saved"
    assert store.records[0].content == "不吃香菜"


def test_forget_and_new_fact_preserves_both_receipts_without_readding_deleted_fact(chat):
    service, store, memories = chat
    store.records = [memory(1, "不吃鸡蛋", "disliked_ingredient")]
    extracting_constraint(memories)
    response = send(service, "忘掉不吃鸡蛋的记忆，用鸡蛋推荐一道菜，不过我不吃香菜")
    assert response.memory_write.deleted_ids == [1]
    assert [row.content for row in response.memory_write.saved] == ["不吃香菜"]
    assert [row.content for row in store.records] == ["不吃香菜"]
    assert "已删除" in response.answer and "已保存长期记忆：不吃香菜" in response.answer
    assert memories.extractor.inputs[0][0]["content"] == "用鸡蛋推荐一道菜，不过我不吃香菜"


def test_explicit_withdrawal_does_not_clear_unmentioned_allergy(chat):
    service, store, _ = chat
    store.records = [memory(1, "不吃香菜", "disliked_ingredient"), memory(2, "香菜过敏", "allergy")]
    response = send(service, "我以前不吃香菜，现在可以了")
    assert response.memory_write.deleted_ids == [1]
    assert store.records[0].type == "allergy"


def test_explicit_operation_without_memory_service_has_precise_failure(chat):
    service, store, _ = chat
    service.memory_service = None
    response = send(service, "忘掉清淡偏好")
    assert response.memory_write.status == "unavailable"
    assert "服务不可用" in response.answer and "已删除" not in response.answer
    assert store.writes == []


def test_question_and_greeting_cannot_authorize_destructive_operation(chat):
    service, store, _ = chat
    store.records = [memory(1, "喜欢清淡")]
    send(service, "怎么忘掉清淡偏好？")
    assert store.writes == []


@pytest.mark.parametrize("text,status", [("忘掉清淡偏好", "deleted"), ("把清淡偏好改成喜欢麻辣", "failed")])
def test_deletion_does_not_require_embedding_but_update_does(chat, text, status):
    service, store, memories = chat
    store.records = [memory(1, "喜欢清淡")]
    memories.embedder = None
    response = send(service, text)
    assert response.memory_write.status == status
    assert bool(store.records) == (status == "failed")


def test_forged_message_id_does_not_authorize_deletion():
    store = Store([memory(1, "喜欢清淡")])
    message = {"id": 999, "user_id": "u", "conversation_id": "c", "role": "user", "content": "忘掉清淡偏好"}
    receipt = asyncio.run(apply_memory_command(memory_service(store), user_id="u", message=message,
                                               command=parse_memory_command(message["content"])))
    assert receipt.status == "rejected" and store.writes == []
