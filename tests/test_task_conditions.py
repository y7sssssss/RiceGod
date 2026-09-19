import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from ricegod.application.agents import planner, recipe_retrieval
from ricegod.config import settings
from ricegod.application.agents import workflow
from ricegod.application.agents.state import AgentState, PlannedTask, QueryPlan, Router
from ricegod.domain.recipes import RecipeConstraints, extract_constraints
from ricegod.infrastructure.recipe_repository import RecipeRepository


class Catalog:
    flavors = ["清淡", "麻辣"]
    methods = ["炒", "蒸"]

    @classmethod
    def extract_constraints(cls, question, memories):
        return extract_constraints(question, ingredients=["鸡蛋", "土豆", "香菜", "花生", "辣椒"],
            flavors=cls.flavors, methods=cls.methods, dish_types=[],
            dish_names=["鸡蛋蒸羹", "土豆丝"], memories=memories)


class Planner:
    def __init__(self, tasks):
        self.tasks = tasks

    def with_structured_output(self, schema):
        return self

    async def ainvoke(self, messages):
        # Global conditions and rewritten quantities must not override task scopes.
        return QueryPlan(tasks=self.tasks,
                         constraints=RecipeConstraints(required_ingredients=["鸡蛋", "土豆"]))


def plan_for(monkeypatch, question, tasks=None, memories=None):
    monkeypatch.setattr(settings, "LLM_API_KEY", "test" if tasks else None)
    state = AgentState(messages=[HumanMessage(content=question)],
                       router=Router(type=["recipe_query"]), user_memories=memories or [])
    return asyncio.run(planner.make_plan(state, Catalog(), lambda **kw: Planner(tasks)))


def test_independent_ingredients_and_counts_are_grounded_in_original_spans(monkeypatch):
    first, second = "早餐用鸡蛋推荐两道菜", "晚餐用土豆推荐三道菜"
    plan = plan_for(monkeypatch, first + "，" + second, [
        PlannedTask(id="breakfast", route="recipe_query", question="推荐一道鸡蛋菜", source_text=first),
        PlannedTask(id="dinner", route="recipe_query", question="推荐一道土豆菜", source_text=second)])
    assert plan.tasks[0].constraints.required_ingredients == ["鸡蛋"]
    assert plan.tasks[1].constraints.required_ingredients == ["土豆"]
    assert [task.recipe_request.limit for task in plan.tasks] == [2, 3]
    assert plan.constraints.required_ingredients == []


def test_common_restrictions_and_memory_apply_to_every_task(monkeypatch):
    plan = plan_for(monkeypatch, "我不吃辣，早餐用鸡蛋，晚餐用土豆，各推荐一道菜。",
                    memories=[{"type": "allergy", "content": "我对花生过敏"},
                              {"type": "disliked_ingredient", "content": "不吃香菜"}])
    assert len(plan.tasks) == 2
    for task in plan.tasks:
        assert task.constraints.avoid_spicy
        assert set(task.constraints.excluded_ingredients) == {"花生", "香菜"}
        assert task.recipe_request.limit == 1
    assert plan.tasks[0].constraints.required_ingredients == ["鸡蛋"]
    assert plan.tasks[1].constraints.required_ingredients == ["土豆"]


def test_local_exclusion_and_flavor_do_not_leak_to_other_task(monkeypatch):
    plan = plan_for(monkeypatch, "早餐用鸡蛋不要土豆不吃辣，晚餐用土豆做麻辣菜，各推荐一道菜。")
    first, second = plan.tasks
    assert first.constraints.excluded_ingredients == ["土豆"]
    assert first.constraints.avoid_spicy
    assert second.constraints.excluded_ingredients == []
    assert not second.constraints.avoid_spicy
    assert second.constraints.required_ingredients == ["土豆"]
    assert second.constraints.flavors == ["麻辣"]


def test_named_dish_queries_keep_independent_dish_names(monkeypatch):
    plan = plan_for(monkeypatch, "早餐鸡蛋蒸羹怎么做，晚餐土豆丝怎么做？")
    assert [task.constraints.dish_name for task in plan.tasks] == ["鸡蛋蒸羹", "土豆丝"]
    assert all(task.recipe_request.limit == 1 and task.recipe_request.detailed for task in plan.tasks)


def test_recipe_task_disambiguates_a_dish_from_a_different_culture_dish(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", "test")
    state = AgentState(messages=[HumanMessage(content="鸡蛋蒸羹怎么做，土豆丝有什么历史？")],
                       router=Router(type=["recipe_query", "food_knowledge"]))
    model = Planner([
        PlannedTask(id="recipe", route="recipe_query", question="鸡蛋蒸羹怎么做"),
        PlannedTask(id="culture", route="food_knowledge", question="土豆丝有什么历史")])
    plan = asyncio.run(planner.make_plan(state, Catalog(), lambda **kwargs: model))
    assert plan.tasks[0].constraints.dish_name == "鸡蛋蒸羹"


@pytest.mark.parametrize("source", ["改写的早餐问题", "早餐用鸡蛋，晚餐用土豆，各推荐一道菜。"])
def test_invalid_or_overlapping_sources_fall_back_to_separated_meals(monkeypatch, source):
    question = "早餐用鸡蛋，晚餐用土豆，各推荐一道菜。"
    plan = plan_for(monkeypatch, question, [
        PlannedTask(id="a", route="recipe_query", question="早餐", source_text=source),
        PlannedTask(id="b", route="recipe_query", question="晚餐", source_text="晚餐用土豆")])
    assert [task.id for task in plan.tasks] == ["meal_0", "meal_1"]
    assert [task.constraints.required_ingredients for task in plan.tasks] == [["鸡蛋"], ["土豆"]]
    assert [task.recipe_request.limit for task in plan.tasks] == [1, 1]


def test_single_task_keeps_combined_ingredient_requirement_and_original_count(monkeypatch):
    plan = plan_for(monkeypatch, "用鸡蛋和土豆推荐五道菜", [
        PlannedTask(id="a", route="recipe_query", question="推荐一道菜")])
    assert set(plan.tasks[0].constraints.required_ingredients) == {"鸡蛋", "土豆"}
    assert plan.tasks[0].recipe_request.limit == 5


def test_short_quotations_cannot_omit_meal_counts_or_shared_count(monkeypatch):
    plan = plan_for(monkeypatch, "早餐用鸡蛋推荐两道菜，晚餐用土豆推荐三道菜", [
        PlannedTask(id="a", route="recipe_query", question="一道菜", source_text="早餐用鸡蛋"),
        PlannedTask(id="b", route="recipe_query", question="一道菜", source_text="晚餐用土豆")])
    assert [task.recipe_request.limit for task in plan.tasks] == [2, 3]
    plan = plan_for(monkeypatch, "早餐用鸡蛋，晚餐用土豆，各推荐一道菜。", [
        PlannedTask(id="a", route="recipe_query", question="早餐", source_text="早餐用鸡蛋"),
        PlannedTask(id="b", route="recipe_query", question="晚餐", source_text="晚餐用土豆，各推荐一道菜。")])
    assert [task.recipe_request.limit for task in plan.tasks] == [1, 1]


def test_current_allergy_inside_one_scope_is_still_shared(monkeypatch):
    first, second = "早餐用鸡蛋，我对花生过敏", "晚餐用土豆"
    plan = plan_for(monkeypatch, first + "；" + second, [
        PlannedTask(id="a", route="recipe_query", question=first, source_text=first),
        PlannedTask(id="b", route="recipe_query", question=second, source_text=second)])
    assert all("花生" in task.constraints.excluded_ingredients for task in plan.tasks)


@pytest.mark.parametrize("fallback", [False, True])
def test_retrieval_uses_each_task_conditions_on_graph_and_local_fallback(tmp_path, monkeypatch, fallback):
    plan = plan_for(monkeypatch, "早餐用鸡蛋推荐一道菜，晚餐用土豆推荐两道菜。")
    rows = [{"name": name, "主食材": [[ingredient, "适量"]], "辅料": [],
             "口味": "清淡", "工艺": "炒", "耗时": "十分钟", "做法": "1:炒熟"}
            for name, ingredient in [("鸡蛋小炒", "鸡蛋"), ("土豆小炒", "土豆"), ("土豆煎饼", "土豆")]]
    path = tmp_path / "recipes.json"
    path.write_text(json.dumps({row["name"]: row for row in rows}, ensure_ascii=False), encoding="utf-8")
    local = RecipeRepository(str(path))
    seen = []

    class Graph(Catalog):
        def query_ranked_candidates(self, constraints, request):
            seen.append((constraints.required_ingredients, request.limit))
            if fallback:
                raise RuntimeError("graph unavailable")
            return [{**row, "菜名": row["name"]} for row in rows]

    monkeypatch.setattr(settings, "LLM_API_KEY", "test")
    results = [asyncio.run(recipe_retrieval.retrieve_recipe(task, plan, Graph(), local, None)) for task in plan.tasks]
    assert seen == [(["鸡蛋"], 1), (["土豆"], 2)]
    assert [len(result.records) for result in results] == [1, 2]
    assert all(task.constraints.matches(row) for task, result in zip(plan.tasks, results) for row in result.records)
    assert all(result.status == ("degraded" if fallback else "ok") for result in results)


def test_actual_graph_synthesizes_with_per_task_conditions(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", None)
    captured = []

    async def retrieve(task, *args):
        from ricegod.application.agents.state import TaskResult
        captured.append((task.id, task.constraints.required_ingredients, task.recipe_request.limit))
        return TaskResult(task_id=task.id, route=task.route, question=task.question,
                          content=task.source_text, sources=[task.id])

    monkeypatch.setattr(recipe_retrieval, "retrieve_recipe", retrieve)
    graph = workflow.build_graph(lambda state: {"router": Router(type=["recipe_query"])},
                                None, None, None, Catalog(), None)
    result = asyncio.run(graph.ainvoke({"messages": [HumanMessage(
        content="早餐用鸡蛋，晚餐用土豆，各推荐一道菜。") ]},
        {"configurable": {"thread_id": "independent-conditions"}}))
    assert sorted(captured) == [("meal_0", ["鸡蛋"], 1), ("meal_1", ["土豆"], 1)]
    assert result["sources"] == ["meal_0", "meal_1"]
