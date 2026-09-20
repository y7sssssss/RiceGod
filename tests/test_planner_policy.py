import asyncio

import pytest
from langchain_core.messages import HumanMessage

from ricegod.application.agents import planner
from ricegod.application.agents.state import AgentState, PlannedTask, QueryPlan, RecipeOperationSpec
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
    state = AgentState(messages=[HumanMessage(content=question)])
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
    state = AgentState(messages=[HumanMessage(content=question)])
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
    state = AgentState(messages=[HumanMessage(content="西红柿炒鸡蛋和红烧肉怎么做")])
    plan = asyncio.run(planner.make_plan(state, catalog, lambda **kwargs: Model()))
    assert len(calls) == 1 and len(plan.tasks) == 2
    assert [t.constraints.dish_name for t in plan.tasks] == ["西红柿炒鸡蛋", "红烧肉"]


@pytest.mark.parametrize("question,route", [
    ("你好", "general_query"),
    ("推荐一个", "additional_query"),
])
def test_simple_conversation_routes_skip_planner_model(monkeypatch, catalog, question, route):
    monkeypatch.setattr(settings, "LLM_API_KEY", "test")

    def forbidden(**kwargs):
        pytest.fail("High-confidence conversation routing must not call the model")

    plan = asyncio.run(planner.make_plan(
        AgentState(messages=[HumanMessage(content=question)]), catalog, forbidden))

    assert [task.route for task in plan.tasks] == [route]
    assert plan.decision_source == "rules"
    assert planner.router_summary(plan, question).type == [route]


def test_conflicting_recipe_constraints_skip_model_and_request_clarification(monkeypatch, catalog):
    monkeypatch.setattr(settings, "LLM_API_KEY", "test")

    def forbidden(**kwargs):
        pytest.fail("A deterministic ingredient conflict must not call the model")

    question = "用鸡蛋推荐菜，但不要鸡蛋"
    plan = asyncio.run(planner.make_plan(
        AgentState(messages=[HumanMessage(content=question)]), catalog, forbidden))

    assert [task.route for task in plan.tasks] == ["additional_query"]
    assert plan.decision_source == "rules"
    assert "矛盾" in plan.clarification and plan.clarification.endswith("？")


def test_shared_allergy_is_not_misclassified_as_a_positive_negative_conflict(catalog):
    question = "我对花生过敏；早餐用鸡蛋推荐一道菜，晚餐用土豆推荐一道菜"

    plan = asyncio.run(planner.make_plan(
        AgentState(messages=[HumanMessage(content=question)]), catalog, None))

    assert [task.route for task in plan.tasks] == ["recipe_query", "recipe_query"]
    assert plan.clarification is None
    assert all("花生" in task.constraints.excluded_ingredients for task in plan.tasks)


def test_different_meals_can_have_opposite_ingredient_conditions(catalog):
    question = "早餐用鸡蛋不要土豆，晚餐用土豆推荐一道菜"

    plan = asyncio.run(planner.make_plan(
        AgentState(messages=[HumanMessage(content=question)]), catalog, None))

    assert len(plan.tasks) == 2
    assert plan.clarification is None


def test_complex_operations_use_one_model_call_and_validate_targets(monkeypatch, catalog):
    monkeypatch.setattr(settings, "LLM_API_KEY", "test")
    question = "找香肠炒菜干的同类菜，另外把玉米淀粉换成马铃薯淀粉"
    calls = []

    class Model:
        def with_structured_output(self, schema):
            return self

        async def ainvoke(self, messages):
            calls.append(messages)
            return QueryPlan(tasks=[
                PlannedTask(id="similar", route="recipe_query", question="找香肠炒菜干的同类菜",
                    source_text="找香肠炒菜干的同类菜",
                    operation_spec=RecipeOperationSpec(operation="similar", dish_name="香肠炒菜干")),
                PlannedTask(id="substitute", route="recipe_query", question="把玉米淀粉换成马铃薯淀粉",
                    source_text="把玉米淀粉换成马铃薯淀粉",
                    operation_spec=RecipeOperationSpec(operation="substitute", ingredient="玉米淀粉",
                                                       replacement="马铃薯淀粉")),
            ])

    plan = asyncio.run(planner.make_plan(
        AgentState(messages=[HumanMessage(content=question)]), catalog, lambda **kwargs: Model()))

    assert len(calls) == 1
    assert [task.recipe_request.operation for task in plan.tasks] == ["similar", "substitute"]
    assert plan.tasks[0].operation_spec.dish_name == "香肠炒菜干"
    assert plan.tasks[1].operation_spec.model_dump(exclude={"operation"}) == {
        "dish_name": None, "ingredient": "玉米淀粉", "replacement": "马铃薯淀粉"}
    assert all(task.operation_error is None for task in plan.tasks)


@pytest.mark.parametrize("question,expected", [
    ("找一道同类菜", "请明确要查询哪道菜的相似菜。"),
    ("鸡蛋和豆腐有什么平替", "检测到多个可能的食材，请明确要替代哪一种食材。"),
])
def test_unresolved_operation_target_becomes_clarification(catalog, question, expected):
    plan = QueryPlan(tasks=[PlannedTask(id="recipe", route="recipe_query",
                                        question=question, source_text=question)])

    grounded = ground_task_conditions(plan, question, catalog, [])

    assert grounded.tasks[0].operation_error == expected


def test_unknown_planner_operation_target_is_rejected(catalog):
    question = "找香肠炒菜干的相似菜"
    plan = QueryPlan(tasks=[PlannedTask(id="recipe", route="recipe_query",
        question=question, source_text=question,
        operation_spec=RecipeOperationSpec(operation="similar", dish_name="模型编造菜"))])

    grounded = ground_task_conditions(plan, question, catalog, [])

    assert grounded.tasks[0].operation_spec.dish_name == "香肠炒菜干"
    assert grounded.tasks[0].operation_error is None


def test_unknown_request_without_model_falls_back_to_clarification(monkeypatch, catalog):
    monkeypatch.setattr(settings, "LLM_API_KEY", None)

    plan = asyncio.run(planner.make_plan(
        AgentState(messages=[HumanMessage(content="今天心情不错")]), catalog, None))

    assert [task.route for task in plan.tasks] == ["additional_query"]
    assert plan.decision_source == "fallback"


def test_planner_exception_falls_back_to_safe_clarification(monkeypatch, catalog):
    monkeypatch.setattr(settings, "LLM_API_KEY", "test")

    class BrokenModel:
        def with_structured_output(self, schema):
            return self

        async def ainvoke(self, messages):
            raise RuntimeError("provider unavailable")

    plan = asyncio.run(planner.make_plan(
        AgentState(messages=[HumanMessage(content="今天心情不错")]),
        catalog,
        lambda **kwargs: BrokenModel(),
    ))

    assert [task.route for task in plan.tasks] == ["additional_query"]
    assert plan.decision_source == "fallback"


def test_two_ingredient_substitution_uses_planner_once(monkeypatch, catalog):
    monkeypatch.setattr(settings, "LLM_API_KEY", "test")
    question = "用马铃薯淀粉代替玉米淀粉"
    calls = []

    class Model:
        def with_structured_output(self, schema):
            return self

        async def ainvoke(self, messages):
            calls.append(messages)
            return QueryPlan(tasks=[PlannedTask(
                id="substitute",
                route="recipe_query",
                question=question,
                source_text=question,
                operation_spec=RecipeOperationSpec(
                    operation="substitute",
                    ingredient="玉米淀粉",
                    replacement="马铃薯淀粉",
                ),
            )])

    plan = asyncio.run(planner.make_plan(
        AgentState(messages=[HumanMessage(content=question)]),
        catalog,
        lambda **kwargs: Model(),
    ))

    assert len(calls) == 1
    assert plan.tasks[0].operation_spec.ingredient == "玉米淀粉"
    assert plan.tasks[0].operation_spec.replacement == "马铃薯淀粉"
