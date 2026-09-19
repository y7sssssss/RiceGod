import asyncio
import json

import pytest
from langchain_core.messages import HumanMessage

from ricegod.application.agents import planner, recipe_retrieval
from ricegod.config import settings
from ricegod.application.agents.state import AgentState, PlannedTask, QueryPlan, RecommendationGroup, Router, TaskResult
from ricegod.domain.recipe_ranking import (
    CANDIDATE_LIMIT, RecipeRequest, build_recipe_request, rank_recipes,
)
from ricegod.domain.recipes import RecipeConstraints
from ricegod.infrastructure.recipe_graph_repository import RecipeGraphRepository
from ricegod.infrastructure.recipe_repository import RecipeRepository


def recipe(name, *, main=None, aux=None, time="十分钟", method="炒", flavor="咸鲜", steps=4):
    return {"name": name, "主食材": main if main is not None else [["鸡蛋", "2个"]],
            "辅料": aux or [], "耗时": time, "工艺": method, "口味": flavor,
            "类型": "家常菜", "做法": "".join(f"{i}:操作步骤。" for i in range(1, steps + 1))}


@pytest.mark.parametrize("query,limit", [
    ("用鸡蛋推荐家常菜", 3), ("推荐一道鸡蛋菜", 1), ("推荐一个菜", 1),
    ("推荐五道鸡蛋菜", 5), ("推荐2种菜", 2), ("推荐十道菜", 10),
    ("推荐十二道菜", 10), ("用三个鸡蛋推荐家常菜", 3), ("第二道菜怎么做", 1),
])
def test_count_comes_from_dish_quantity_not_ingredient_or_ordinal(query, limit):
    request = build_recipe_request(query, RecipeConstraints())
    assert request.limit == limit


def test_named_dish_lookup_returns_one_and_honors_requested_details():
    request = build_recipe_request("鸡蛋羹怎么做", RecipeConstraints(dish_name="鸡蛋羹"))
    assert request.limit == 1
    assert not request.recommendation
    assert request.detailed


def test_graph_operations_are_distinct_from_normal_recommendations():
    similar = build_recipe_request(
        "找三道和西红柿炒鸡蛋相似的菜",
        RecipeConstraints(dish_name="西红柿炒鸡蛋"),
    )
    substitute = build_recipe_request(
        "玉米淀粉可以用什么替代",
        RecipeConstraints(required_ingredients=["玉米淀粉"]),
    )

    assert similar.operation == "similar"
    assert similar.recommendation and similar.limit == 3
    assert substitute.operation == "substitute"
    assert not substitute.recommendation


def test_main_ingredient_is_preferred_to_auxiliary_ingredient():
    main = recipe("鸡蛋主料菜")
    aux = recipe("鸡蛋辅料菜", main=[["面粉", "200克"]], aux=[["鸡蛋", "1个"]])
    selected = rank_recipes([aux, main], RecipeConstraints(required_ingredients=["鸡蛋"]), RecipeRequest(limit=1))
    assert selected[0]["name"] == "鸡蛋主料菜"
    assert selected[0]["推荐理由"][0] == "指定食材用于主料：鸡蛋"


def test_shorter_time_and_fewer_steps_rank_above_slow_complex_recipe():
    rows = [recipe("鸡蛋发酵面饼", time="数小时", steps=12), recipe("鸡蛋快炒", steps=5),
            recipe("鸡蛋简炒", steps=2)]
    selected = rank_recipes(rows, RecipeConstraints(required_ingredients=["鸡蛋"]), RecipeRequest(limit=1))
    assert selected[0]["name"] == "鸡蛋简炒"


def test_unknown_time_is_not_presented_as_fast_or_invented():
    selected = rank_recipes([recipe("鸡蛋菜", time="")], RecipeConstraints(), RecipeRequest())
    assert selected[0]["耗时"] == ""
    assert not any("耗时" in reason or "快手" in reason for reason in selected[0]["推荐理由"])


def test_user_flavor_preference_changes_order_without_excluding_other_flavors():
    rows = [recipe("A鸡蛋菜", flavor="咸鲜"), recipe("B鸡蛋菜", flavor="葱香")]
    request = build_recipe_request("推荐鸡蛋菜", RecipeConstraints(required_ingredients=["鸡蛋"]),
        memories=[{"type": "liked_cuisine", "content": "我喜欢葱香口味"}], flavors=["葱香", "咸鲜"])
    selected = rank_recipes(rows, RecipeConstraints(required_ingredients=["鸡蛋"]), request)
    assert [row["name"] for row in selected] == ["B鸡蛋菜", "A鸡蛋菜"]
    assert request.preferred_flavors == ["葱香"]


def test_negative_memory_is_not_treated_as_positive_flavor_preference():
    request = build_recipe_request("推荐菜", RecipeConstraints(),
        memories=[{"type": "dietary_preference", "content": "我不喜欢香辣"}], flavors=["香辣"])
    assert request.preferred_flavors == []


def test_hard_filters_precede_ranking_and_do_not_fill_with_invalid_recipes():
    rows = [recipe("鸡蛋香菜菜", aux=[["香菜", "少许"]]),
            recipe("鸡蛋辣菜", aux=[["辣椒", "少许"]]), recipe("鸡蛋清炒"),
            recipe("鸡蛋花生菜", aux=[["花生", "少许"]])]
    constraints = RecipeConstraints(required_ingredients=["鸡蛋"], excluded_ingredients=["香菜", "花生"], avoid_spicy=True)
    selected = rank_recipes(rows, constraints, RecipeRequest(limit=3))
    assert [row["name"] for row in selected] == ["鸡蛋清炒"]
    assert rank_recipes(rows, RecipeConstraints(required_ingredients=["不存在的食材"]), RecipeRequest()) == []


def test_similar_recipes_are_penalized_to_offer_different_methods():
    rows = [recipe("A鸡蛋炒菜"), recipe("B鸡蛋炒菜"), recipe("C鸡蛋蒸菜", method="蒸")]
    selected = rank_recipes(rows, RecipeConstraints(required_ingredients=["鸡蛋"]), RecipeRequest(limit=2))
    assert [row["name"] for row in selected] == ["A鸡蛋炒菜", "C鸡蛋蒸菜"]


def test_decorated_and_common_alias_names_are_deduplicated():
    rows = [recipe("#美食#番茄炒蛋"), recipe("西红柿炒鸡蛋"), recipe("鸡蛋羹", method="蒸")]
    selected = rank_recipes(rows, RecipeConstraints(), RecipeRequest(limit=3))
    assert len(selected) == 2


def test_ties_have_stable_order_independent_of_database_row_order():
    rows = [recipe("B鸡蛋菜"), recipe("A鸡蛋菜"), recipe("C鸡蛋菜")]
    constraints, request = RecipeConstraints(), RecipeRequest()
    assert rank_recipes(rows, constraints, request) == rank_recipes(list(reversed(rows)), constraints, request)


def catalog(tmp_path):
    rows = [recipe("#标签#鸡蛋慢面饼", time="数小时", method="煎"),
            recipe("鸡蛋快炒"), recipe("鸡蛋蒸羹", method="蒸"), recipe("鸡蛋快煮", method="煮"),
            recipe("鸡蛋香菜菜", aux=[["香菜", "少许"]])]
    path = tmp_path / "recipes.json"
    path.write_text(json.dumps({row["name"]: row for row in rows}, ensure_ascii=False), encoding="utf-8")
    return rows, RecipeGraphRepository(data_path=path), RecipeRepository(data_path=path)


def test_graph_preselects_by_relevance_before_candidate_limit(tmp_path):
    _, graph, _ = catalog(tmp_path)
    calls = []

    class Client:
        def run_query(self, cypher, params, **kwargs):
            calls.append((cypher, params, kwargs))
            return []

    graph.client = Client()
    graph.query_ranked_candidates(RecipeConstraints(required_ingredients=["鸡蛋"], excluded_ingredients=["香菜"]),
        RecipeRequest(prefer_quick=True, preferred_flavors=["葱香"]))
    cypher, params, kwargs = calls[0]
    assert cypher.index("WHERE all(wanted") < cypher.index("AS ranking_score") < cypher.index("LIMIT $limit")
    assert "ORDER BY ranking_score DESC, dish.name" in cypher
    assert params["limit"] == CANDIDATE_LIMIT
    assert params["ranking_ingredients"] == ["鸡蛋"]
    assert params["excluded_ingredients"] == ["香菜"]
    assert params["ranking_time_weight"] == 2
    assert kwargs["max_records"] == CANDIDATE_LIMIT and kwargs["read_only"]


def test_planner_rewrite_cannot_change_original_dish_count(tmp_path, monkeypatch):
    _, graph, _ = catalog(tmp_path)
    monkeypatch.setattr(settings, "LLM_API_KEY", "test")

    class Model:
        def with_structured_output(self, schema):
            return self

        async def ainvoke(self, messages):
            return QueryPlan(tasks=[PlannedTask(id="recipe", route="recipe_query", question="推荐一道鸡蛋菜"),
                                   PlannedTask(id="culture", route="food_knowledge", question="川菜的历史")],
                             recipe_request=RecipeRequest(query="wrong", limit=1))

    question = "推荐五道鸡蛋菜，并介绍川菜的历史"
    state = AgentState(messages=[HumanMessage(content=question)], router=Router(type=["recipe_query", "food_knowledge"]))
    plan = asyncio.run(planner.make_plan(state, graph, lambda **kwargs: Model()))
    assert plan.recipe_request.limit == 5
    assert plan.recipe_request.query == question


def test_graph_and_fallback_share_order_quantity_and_hard_filters(tmp_path, monkeypatch):
    rows, graph, local = catalog(tmp_path)
    monkeypatch.setattr(settings, "LLM_API_KEY", "test")
    constraints = RecipeConstraints(required_ingredients=["鸡蛋"], excluded_ingredients=["香菜"])
    request = build_recipe_request("用鸡蛋推荐家常菜，不要香菜", constraints)
    plan = QueryPlan(constraints=constraints, recipe_request=request)
    task = PlannedTask(id="recipe", route="recipe_query", question="推荐一道鸡蛋菜")
    graph.query_ranked_candidates = lambda *args: [{**row, "菜名": row["name"]} for row in reversed(rows)]
    normal = asyncio.run(recipe_retrieval.retrieve_recipe(task, plan, graph, local, None))

    def unavailable(*args):
        raise RuntimeError("graph unavailable")

    graph.query_ranked_candidates = unavailable
    fallback = asyncio.run(recipe_retrieval.retrieve_recipe(task, plan, graph, local, None))
    assert normal.status == "ok" and fallback.status == "degraded"
    assert [row["name"] for row in normal.records] == [row["name"] for row in fallback.records]
    assert len(normal.records) == 3
    assert all(constraints.matches(row) for row in normal.records)
    assert "鸡蛋慢面饼" not in [row["name"] for row in normal.records]
    assert "做法:" not in fallback.content


def test_requested_recipe_details_are_preserved_in_local_response(tmp_path, monkeypatch):
    _, graph, local = catalog(tmp_path)
    monkeypatch.setattr(settings, "LLM_API_KEY", None)
    constraints = RecipeConstraints(required_ingredients=["鸡蛋"])
    task = PlannedTask(id="recipe", route="recipe_query", question="推荐两道鸡蛋菜，给出材料和步骤")
    result = asyncio.run(recipe_retrieval.retrieve_recipe(task, QueryPlan(constraints=constraints), graph, local, None))
    assert len(result.records) == 2
    assert "做法:" in result.content


def test_user_can_select_second_recipe_from_previous_ranked_results(tmp_path, monkeypatch):
    _, graph, local = catalog(tmp_path)
    monkeypatch.setattr(settings, "LLM_API_KEY", None)
    state = AgentState(messages=[HumanMessage(content="第二道菜怎么做？给出材料和步骤。")],
        router=Router(type=["recipe_query"]),
        last_recommendations=[RecommendationGroup(task_id="recipe", records=[
            recipe("鸡蛋快炒"), recipe("鸡蛋蒸羹", method="蒸")])])
    plan = asyncio.run(planner.make_plan(state, graph, None))
    assert plan.constraints.dish_name == "鸡蛋蒸羹"
    assert plan.recipe_request.limit == 1
    result = asyncio.run(recipe_retrieval.retrieve_recipe(plan.tasks[0], plan, graph, local, None))
    assert [row["name"] for row in result.records] == ["鸡蛋蒸羹"]
    assert "做法:" in result.content
