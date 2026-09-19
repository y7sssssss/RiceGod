import asyncio

import pytest
from langchain_core.messages import HumanMessage

from ricegod.application.agents import planner
from ricegod.application.agents.state import AgentState, PlannedTask, QueryPlan, Router
from ricegod.application.agents.task_conditions import ground_task_conditions
from ricegod.application.performance import performance_scope
from ricegod.config import settings


@pytest.mark.parametrize("question,routes,counts,reason", [
    ("用鸡蛋推荐三道家常菜", ["recipe_query"], [3], "single_recipe"),
    ("用土豆和鸡蛋推荐两道家常菜", ["recipe_query"], [2], "single_recipe"),
    ("用鸡蛋推荐十二道家常菜，不要香菜", ["recipe_query"], [10], "single_recipe"),
    ("西红柿炒鸡蛋怎么做", ["recipe_query"], [1], "single_recipe"),
    ("早餐用鸡蛋推荐两道菜，晚餐用土豆推荐三道菜", ["recipe_query"], [2, 3], "explicit_meals"),
    ("介绍川菜的历史与特点", ["food_knowledge"], [], "single_culture"),
    ("推荐一道鸡蛋家常菜，并介绍这道菜的来历", ["recipe_query", "food_knowledge"], [1], "dependent_lookup"),
])
def test_explicit_requests_skip_planner_model_without_losing_conditions(monkeypatch, catalog, question, routes, counts, reason):
    monkeypatch.setattr(settings, "LLM_API_KEY", "test")
    def forbidden(**kwargs):
        pytest.fail("This explicit request should not invoke the planning model")
    state = AgentState(messages=[HumanMessage(content=question)], router=Router(type=routes))
    with performance_scope() as timings:
        plan = asyncio.run(planner.make_plan(state, catalog, forbidden))
    recipes = [task for task in plan.tasks if task.route == "recipe_query"]
    assert [task.recipe_request.limit for task in recipes] == counts
    assert {task.route for task in plan.tasks} == set(routes)
    assert any(event == {"kind":"planner", "mode":"rules", "reason":reason} for event in timings.report().events)
    if reason == "dependent_lookup":
        assert plan.tasks[-1].depends_on == [recipes[0].id]
    if reason == "explicit_meals":
        assert [task.constraints.required_ingredients for task in recipes] == [["鸡蛋"], ["土豆"]]
    if "不要香菜" in question:
        assert "香菜" in recipes[0].constraints.excluded_ingredients


def test_complex_request_still_uses_model_and_recovers_full_local_spans(monkeypatch, catalog):
    monkeypatch.setattr(settings, "LLM_API_KEY", "test")
    question = "分别用鸡蛋推荐两道清淡家常菜，不要香菜；用土豆推荐三道麻辣家常菜，不要花生"
    calls = []
    class Model:
        def with_structured_output(self, schema):
            return self
        async def ainvoke(self, messages):
            calls.append(messages)
            # Two overlapping whole-turn quotes can be repaired from distinct exact subquestions.
            return QueryPlan(tasks=[
                PlannedTask(id="a", route="recipe_query", question="用鸡蛋推荐两道清淡家常菜", source_text=question),
                PlannedTask(id="b", route="recipe_query", question="用土豆推荐三道麻辣家常菜", source_text=question)])
    state = AgentState(messages=[HumanMessage(content=question)], router=Router(type=["recipe_query"]))
    with performance_scope() as timings:
        plan = asyncio.run(planner.make_plan(state, catalog, lambda **kwargs: Model()))
    assert len(calls) == 1
    assert [t.recipe_request.limit for t in plan.tasks] == [2, 3]
    assert [t.constraints.required_ingredients for t in plan.tasks] == [["鸡蛋"], ["土豆"]]
    assert [t.constraints.excluded_ingredients for t in plan.tasks] == [["香菜"], ["花生"]]
    assert [t.constraints.flavors for t in plan.tasks] == [["清淡"], ["麻辣"]]
    assert any(e["kind"] == "planner_scope_repair" for e in timings.report().events)
    assert any(e["kind"] == "planner" and e["mode"] == "model" for e in timings.report().events)


def test_meal_scopes_recover_from_overlapping_quotes_without_using_task_order(catalog):
    question = "早餐用鸡蛋推荐两道菜，不吃辣；晚餐用土豆推荐三道麻辣菜"
    plan = QueryPlan(tasks=[
        PlannedTask(id="d", route="recipe_query", question="晚餐菜", source_text=question),
        PlannedTask(id="b", route="recipe_query", question="早餐菜", source_text=question)])
    plan = ground_task_conditions(plan, question, catalog, [])
    assert [t.constraints.required_ingredients for t in plan.tasks] == [["土豆"], ["鸡蛋"]]
    assert [t.recipe_request.limit for t in plan.tasks] == [3, 2]
    assert not plan.tasks[0].constraints.avoid_spicy and plan.tasks[1].constraints.avoid_spicy


def test_unanchored_or_duplicate_model_tasks_are_rejected(catalog):
    question = "分别用鸡蛋推荐两道菜；用土豆推荐三道菜"
    for quoted in (question, "用鸡蛋推荐两道菜", "模型编造的问题"):
        plan = QueryPlan(tasks=[PlannedTask(id=identity, route="recipe_query", question=quoted, source_text=quoted)
                                for identity in ("a", "b")])
        with pytest.raises(ValueError):
            ground_task_conditions(plan, question, catalog, [])


def test_ambiguous_repeated_quote_requires_unique_meal_anchor(catalog):
    question = "早餐用鸡蛋推荐两道菜；晚餐用鸡蛋推荐三道菜"
    plan = QueryPlan(tasks=[PlannedTask(id=identity, route="recipe_query", question="鸡蛋", source_text="鸡蛋")
                            for identity in ("a", "b")])
    with pytest.raises(ValueError):
        ground_task_conditions(plan, question, catalog, [])


def test_multiple_named_dishes_are_not_treated_as_one_query(monkeypatch, catalog):
    monkeypatch.setattr(settings, "LLM_API_KEY", "test")
    calls = []
    class Model:
        def with_structured_output(self, schema):
            return self
        async def ainvoke(self, messages):
            calls.append(messages)
            return QueryPlan(tasks=[
                PlannedTask(id="a", route="recipe_query", question="西红柿炒鸡蛋怎么做", source_text="西红柿炒鸡蛋"),
                PlannedTask(id="b", route="recipe_query", question="红烧肉怎么做", source_text="红烧肉")])
    state = AgentState(messages=[HumanMessage(content="西红柿炒鸡蛋和红烧肉怎么做")], router=Router(type=["recipe_query"]))
    plan = asyncio.run(planner.make_plan(state, catalog, lambda **kwargs: Model()))
    assert len(calls) == 1 and len(plan.tasks) == 2
    assert [t.constraints.dish_name for t in plan.tasks] == ["西红柿炒鸡蛋", "红烧肉"]
