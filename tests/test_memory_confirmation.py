import asyncio
from datetime import datetime, timezone

import pytest
from langchain_core.messages import AIMessage

from ricegod.config import settings
from ricegod.application.chat.models import ChatRequest
from ricegod.application.chat.service import ChatService
from ricegod.application.memory.confirmation import confirm_memory_write
from ricegod.application.memory.models import MemoryRecord, MemoryWriteResult


def record():
    now = datetime.now(timezone.utc)
    return MemoryRecord(id=10, user_id="receipt-user", type="disliked_ingredient", content="不吃香菜",
                        created_at=now, updated_at=now, source_message_id=1, evidence="不吃香菜")


@pytest.mark.parametrize("receipt", [MemoryWriteResult(available=False), MemoryWriteResult(failed_count=1),
    MemoryWriteResult(skipped_count=1), MemoryWriteResult()])
def test_no_unverified_success_claim_survives(receipt):
    answer = confirm_memory_write("我已经记住你的喜好了。推荐鸡蛋蒸羹。", "请记住不吃香菜", receipt)
    assert "已经记住" not in answer and "已保存" not in answer
    assert "推荐鸡蛋蒸羹" in answer
    assert "未能保存" in answer or "未确认" in answer


def test_partial_receipt_only_confirms_contents_actually_saved():
    answer = confirm_memory_write("已将所有喜好保存到长期记忆。", "请记住不吃香菜不能吃辣",
                                  MemoryWriteResult(saved=[record()], failed_count=1))
    assert "已保存长期记忆：不吃香菜" in answer
    assert "不能吃辣" not in answer and "所有喜好" not in answer
    assert "未能保存" in answer


@pytest.mark.parametrize("fail_user_log", [False, True])
def test_service_history_and_checkpoint_use_confirmed_answer(monkeypatch, fail_user_log):
    events = []
    receipt = MemoryWriteResult(saved=[record()])

    class Graph:
        async def aget_state(self, config):
            class Snapshot:
                values = {"messages": [AIMessage(content="old")]}
            return Snapshot()

        async def ainvoke(self, data, config):
            events.append("generate")
            return {"messages": [AIMessage(id="answer-id", content="已记住所有偏好。推荐鸡蛋蒸羹。")],
                    "router": {"type": ["recipe_query"]}}

        async def aupdate_state(self, config, values, as_node):
            events.append(("checkpoint", values))

    class Store:
        def add_message(self, **kwargs):
            if kwargs["role"] == "user" and fail_user_log:
                raise RuntimeError("user log unavailable")
            events.append(("log", kwargs["role"], kwargs["content"]))

    class Service:
        def recall(self, **kwargs):
            return []

        def build_memory_prompt(self, records):
            return ""

        async def extract_and_store_result(self, **kwargs):
            events.append("write_memory")
            return receipt

    service = ChatService(Graph(), Store(), Service())
    response = asyncio.run(service.chat(ChatRequest(user_id="receipt-user", conversation_id="receipt",
                                                   message="请记住我不吃香菜")))
    assert response.memory_write.status == ("unavailable" if fail_user_log else "saved")
    assert "已记住所有偏好" not in response.answer
    assert "推荐鸡蛋蒸羹" in response.answer
    assert ("已保存长期记忆：不吃香菜" in response.answer) == (not fail_user_log)
    if fail_user_log:
        assert "write_memory" not in events  # Never extract a previous user turn instead.
    else:
        assert events.index("write_memory") > events.index("generate")
    saved_answer = next(event[2] for event in events if isinstance(event, tuple) and event[:2] == ("log", "assistant"))
    checkpoint = next(event[1] for event in events if isinstance(event, tuple) and event[0] == "checkpoint")
    assert saved_answer == checkpoint["answer"] == response.answer
    assert checkpoint["messages"][0].id == "answer-id"


def test_initial_graph_checkpoint_never_records_an_unconfirmed_memory_claim(monkeypatch, catalog, local):
    from ricegod.application.agents import workflow
    from langchain_core.messages import HumanMessage

    async def prematurely_acknowledge(state):
        return {"messages": [AIMessage(content="我已经记住你的喜好了。") ]}

    monkeypatch.setattr(settings, "LLM_API_KEY", None)
    graph = workflow.build_graph(prematurely_acknowledge,
        prematurely_acknowledge, None, catalog, local)
    config = {"configurable": {"thread_id": "pending-confirmation"}}
    result = asyncio.run(graph.ainvoke({"messages": [HumanMessage(content="请记住我的偏好")]}, config))
    assert "已记住" not in result["answer"] and "已经记住" not in result["answer"]
    assert graph.get_state(config).values["messages"][-1].content == result["answer"]
