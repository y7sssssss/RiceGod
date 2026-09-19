import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from ricegod.application.agents import conversation, knowledge_retrieval, recipe_retrieval, router, workflow
from ricegod.application.agents.state import Router, TaskResult
from ricegod.application.chat.models import ChatRequest
from ricegod.application.chat.service import ChatExecutionError, ChatService
from ricegod.application.performance import measure_stage, performance_scope, record_event
from ricegod.config import settings


def test_collector_resets_after_errors_and_cancellation():
    for exception, status in [(ValueError("private contents"), "error"), (asyncio.CancelledError(), "cancelled")]:
        with performance_scope() as collector:
            with pytest.raises(type(exception)):
                with measure_stage("test"):
                    record_event("test", mode="fixed")
                    raise exception
        report = collector.report()
        assert report.stages[0].status == status and report.stages[0].duration_ms >= 0
        assert "private contents" not in report.model_dump_json()
    with performance_scope() as fresh:
        assert fresh.report().stages == [] and fresh.report().events == []


def test_failed_chat_still_records_error_and_releases_lock(monkeypatch):
    reports = []

    class Graph:
        async def aget_state(self, cfg):
            return type("Snapshot", (), {"values": {}})()

        async def ainvoke(self, data, config):
            raise RuntimeError("Failure")

    from ricegod.application.chat import service as module
    monkeypatch.setattr(module.logger, "info", lambda *args: reports.append(args))

    async def run():
        service = ChatService(Graph())
        for _ in range(2):
            with pytest.raises(ChatExecutionError):
                await asyncio.wait_for(service.chat(ChatRequest(message="test")), 2)

    asyncio.run(run())
    assert len(reports) == 2 and all('"status":"error"' in args[1] for args in reports)


def test_concurrent_requests_have_separate_trace_and_stage_collections(monkeypatch, catalog, local):
    monkeypatch.setattr(settings, "LLM_API_KEY", None)
    entered, release = asyncio.Event(), asyncio.Event()

    async def intent(state):
        record_event("test", mode=state.question)
        return {"router": Router(type=["recipe_query", "food_knowledge"])}

    async def retrieval(task, *args):
        entered.set()
        await release.wait()
        return TaskResult(task_id=task.id, route=task.route, question=task.question, content="fixture")

    monkeypatch.setattr(recipe_retrieval, "retrieve_recipe", retrieval)
    monkeypatch.setattr(knowledge_retrieval, "retrieve_knowledge", retrieval)
    graph = workflow.build_graph(intent, conversation.general_query, conversation.additional_query,
                                 router._build_llm, catalog, local)
    service = ChatService(graph)

    async def run():
        requests = [asyncio.create_task(service.chat(ChatRequest(user_id=u, message=u))) for u in ("one", "two")]
        await asyncio.wait_for(entered.wait(), 2)
        release.set()
        return await asyncio.wait_for(asyncio.gather(*requests), 3)

    responses = asyncio.run(run())
    assert len({r.performance.trace_id for r in responses}) == 2
    for response, expected in zip(responses, ("one", "two")):
        report = response.performance
        assert [e["mode"] for e in report.events if e["kind"] == "test"] == [expected]
        assert {"router", "planner", "recipe_retrieval", "knowledge_retrieval", "answer", "total"} <= {s.name for s in report.stages}
        assert all(s.duration_ms >= 0 and s.start_ms >= 0 for s in report.stages)
        assert all(s.task_id for s in report.stages if s.name.endswith("retrieval"))
        assert not graph.get_state({"configurable": {"thread_id": '["' + expected + '","default"]'}}).values.get("performance")


def test_parallel_task_intervals_overlap_without_summing_into_total(monkeypatch, catalog, local):
    monkeypatch.setattr(settings, "LLM_API_KEY", None)

    async def run():
        barrier = asyncio.Event()
        entered = []

        async def retrieval(task, *args):
            entered.append(task.id)
            if len(entered) == 2:
                barrier.set()
            await asyncio.wait_for(barrier.wait(), 1)
            await asyncio.sleep(.02)
            return TaskResult(task_id=task.id, route=task.route, question=task.question, content="fixture")

        monkeypatch.setattr(recipe_retrieval, "retrieve_recipe", retrieval)
        monkeypatch.setattr(knowledge_retrieval, "retrieve_knowledge", retrieval)

        async def intent(state):
            return {"router": Router(type=["recipe_query", "food_knowledge"])}

        graph = workflow.build_graph(intent, conversation.general_query, conversation.additional_query,
                                     router._build_llm, catalog, local)
        response = await ChatService(graph).chat(ChatRequest(message="推荐鸡蛋菜并介绍川菜历史"))
        spans = [s for s in response.performance.stages if s.name.endswith("retrieval")]
        assert len(spans) == 2
        assert max(s.start_ms for s in spans) < min(s.start_ms + s.duration_ms for s in spans)
        assert response.performance.total_ms == next(s.duration_ms for s in response.performance.stages if s.name == "total")

    asyncio.run(run())
