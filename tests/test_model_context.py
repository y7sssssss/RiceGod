import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ricegod.application.agents import answer, conversation, planner, router, workflow
from ricegod.application.agents.context import ContextBudgetExceeded, build_model_context, estimate_tokens
from ricegod.application.agents.state import AgentState, PlannedTask, QueryPlan, RecommendationGroup, Router, TaskResult
from ricegod.config import settings
from ricegod.domain.recipe_ranking import RecipeRequest


def long_state():
    messages = [SystemMessage(content="orphan historical instruction"), AIMessage(content="orphan")]
    for i in range(30):
        messages.extend([HumanMessage(content=f"历史问题{i}", id=f"u{i}"),
                         AIMessage(content=f"历史回答{i}", id=f"a{i}")])
    messages.append(HumanMessage(content="当前问题", id="current"))
    return AgentState(messages=messages, memory_context="长期记忆：不吃花生",
                      last_recommendations=[RecommendationGroup(task_id="t", records=[
                          {"name": "鸡蛋羹", "做法": "irrelevant recipe steps"}, {"name": "土豆丝"}])])


@pytest.mark.parametrize("purpose", ["router", "planner", "conversation", "answer"])
def test_window_retains_complete_turns_and_protected_inputs_without_mutating_state(monkeypatch, purpose):
    monkeypatch.setattr(settings, "CONTEXT_RECENT_TURNS", 2)
    state = long_state()
    before = [m.model_dump() for m in state.messages]
    result = build_model_context(state, purpose, system_prompt="系统提示", evidence="本轮证据")
    text = "\n".join(str(m.content) for m in result)
    assert "历史问题27" not in text and "历史问题28" in text and "历史回答28" in text
    assert "orphan" not in text and "irrelevant recipe steps" not in text
    assert "鸡蛋羹" in text and '"number": 2' in text
    assert ("不吃花生" in text) == (purpose != "router")
    assert result[-2].content == "当前问题" and result[-1].content == "本轮证据"
    assert [m.model_dump() for m in state.messages] == before


def test_zero_turns_excludes_all_old_history(monkeypatch):
    monkeypatch.setattr(settings, "CONTEXT_RECENT_TURNS", 0)
    messages = build_model_context(long_state(), "router")
    assert all("历史问题" not in str(m.content) for m in messages)


def test_budget_evicts_oldest_whole_turn_not_current_or_memory(monkeypatch):
    monkeypatch.setattr(settings, "CONTEXT_ANSWER_TOKEN_BUDGET", 300)
    state = AgentState(messages=[HumanMessage(content="旧" * 100), AIMessage(content="旧回答"),
        HumanMessage(content="最近"), AIMessage(content="最近回答"), HumanMessage(content="本轮")],
        memory_context="长期记忆：对花生过敏")
    result = build_model_context(state, "answer", system_prompt="系统提示", evidence="来源：真实证据")
    assert estimate_tokens(result) <= 300
    assert "旧回答" not in str(result) and "最近回答" in str(result)
    assert "花生过敏" in str(result) and "本轮" in str(result) and "真实证据" in str(result)


@pytest.mark.parametrize("field", ["question", "memory", "evidence"])
def test_protected_overflow_is_explicit_and_never_silently_truncates(monkeypatch, field):
    monkeypatch.setattr(settings, "CONTEXT_ANSWER_TOKEN_BUDGET", 256)
    state = AgentState(messages=[HumanMessage(content="问" * 300 if field == "question" else "本轮")],
                       memory_context="限制" * 100 if field == "memory" else "")
    with pytest.raises(ContextBudgetExceeded):
        build_model_context(state, "answer", evidence="证据" * 100 if field == "evidence" else "资料")


def test_current_business_question_replaces_raw_operation_only_in_model_input():
    state = AgentState(messages=[HumanMessage(content="忘掉不吃鸡蛋，推荐鸡蛋菜")], question="推荐鸡蛋菜")
    assert build_model_context(state, "planner")[-1].content == "推荐鸡蛋菜"
    assert state.messages[-1].content == "忘掉不吃鸡蛋，推荐鸡蛋菜"


@pytest.mark.parametrize("marker", ["u28", "missing"])
def test_memory_operation_boundary_blocks_stale_history_but_preserves_current_and_recommendations(marker):
    state = long_state()
    state.context_history_after = marker
    result = build_model_context(state, "answer")
    assert "历史问题28" not in str(result) and "历史回答28" not in str(result)
    assert "当前问题" in str(result) and "鸡蛋羹" in str(result)
    if marker == "u28":
        assert "历史问题29" in str(result)
    else:
        assert "历史问题29" not in str(result)
    assert len(state.messages) == 63


def test_router_and_planner_use_bounded_context(monkeypatch, catalog):
    monkeypatch.setattr(settings, "LLM_API_KEY", "test")
    monkeypatch.setattr(settings, "USE_LLM_ROUTER", True)
    monkeypatch.setattr(settings, "CONTEXT_RECENT_TURNS", 1)
    captured = []

    class Model:
        def with_structured_output(self, schema):
            self.schema = schema
            return self

        async def ainvoke(self, messages):
            captured.append(messages)
            if self.schema is Router:
                return Router(type=["recipe_query", "food_knowledge"])
            return QueryPlan(tasks=[PlannedTask(id="t", route="recipe_query", question="推荐鸡蛋菜"),
                PlannedTask(id="k", route="food_knowledge", question="川菜的历史")])

    monkeypatch.setattr(router, "_build_llm", lambda **kwargs: Model())
    state = long_state()
    state.question = "推荐鸡蛋菜，并介绍川菜的历史"
    state.router = asyncio.run(router.analyze_intent(state))["router"]
    asyncio.run(planner.make_plan(state, catalog, lambda **kwargs: Model()))
    assert len(captured) == 2
    for messages in captured:
        assert "历史问题28" not in str(messages) and "历史问题29" in str(messages)
        assert any(m.content == state.question for m in messages)


@pytest.mark.parametrize("handler", [conversation.general_query, conversation.additional_query])
def test_conversation_overflow_does_not_call_model(monkeypatch, handler):
    monkeypatch.setattr(settings, "LLM_API_KEY", "test")
    monkeypatch.setattr(settings, "CONTEXT_ANSWER_TOKEN_BUDGET", 256)

    class Model:
        async def ainvoke(self, messages):
            pytest.fail("Overflow must not reach provider")

    monkeypatch.setattr(conversation, "_build_llm", lambda **kwargs: Model())
    result = asyncio.run(handler(AgentState(messages=[HumanMessage(content="问" * 1000)])))
    assert "过长" in result["messages"][0].content


def test_answer_overflow_returns_complete_real_evidence(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "test")
    monkeypatch.setattr(settings, "CONTEXT_ANSWER_TOKEN_BUDGET", 256)
    state = AgentState(messages=[HumanMessage(content="来历")],
        plan=QueryPlan(tasks=[PlannedTask(id="t", route="food_knowledge", question="来历")]),
        task_results={"t": TaskResult(task_id="t", route="food_knowledge", question="来历",
            content="真实资料" * 500, sources=["fixture-source"] )})

    class Model:
        async def ainvoke(self, messages):
            pytest.fail("Overflow must not reach provider")

    result = asyncio.run(answer.synthesize_answer(state, lambda **kwargs: Model()))
    assert "真实资料" * 500 in result["answer"]
    assert result["sources"] == ["fixture-source"]


@pytest.mark.parametrize("detailed", [False, True])
def test_answer_evidence_preserves_details_only_when_requested(detailed):
    state = AgentState(messages=[HumanMessage(content="推荐菜")],
        plan=QueryPlan(tasks=[PlannedTask(id="t", route="recipe_query", question="推荐菜",
            recipe_request=RecipeRequest(recommendation=True, detailed=detailed))]),
        task_results={"t": TaskResult(task_id="t", route="recipe_query", question="推荐菜",
            records=[{"name": "鸡蛋羹", "做法": "真实步骤", "主食材": ["鸡蛋"]}],
            content="重复的格式化步骤", sources=["fixture-source"])})
    payload = answer._evidence_results(state)[0]
    assert ("做法" in payload["records"][0]) == detailed
    assert payload["records"][0]["name"] == "鸡蛋羹" and payload["sources"] == ["fixture-source"]
    assert payload["content"] == ""
    assert state.task_results["t"].records[0]["做法"] == "真实步骤"


def test_long_conversation_keeps_full_checkpoint_and_ordinal_selection(monkeypatch, catalog, local):
    monkeypatch.setattr(settings, "LLM_API_KEY", None)
    graph = workflow.build_graph(router.analyze_intent, conversation.general_query,
        conversation.additional_query, router._build_llm, catalog, local)
    cfg = {"configurable": {"thread_id": "context-window-full-history"}}

    async def run():
        original = await graph.ainvoke({"messages": [HumanMessage(content="用鸡蛋推荐三道菜")]}, cfg)
        for _ in range(12):
            await graph.ainvoke({"messages": [HumanMessage(content="谢谢")]}, cfg)
        result = await graph.ainvoke({"messages": [HumanMessage(content="第二道怎么做")]}, cfg)
        assert len(result["messages"]) == 28
        assert result["plan"].selected_recipe_name == original["last_recommendations"][0].records[1]["name"]
        selected = build_model_context(AgentState(**result), "answer")
        assert sum(isinstance(m, AIMessage) for m in selected) <= settings.CONTEXT_RECENT_TURNS

    asyncio.run(run())
