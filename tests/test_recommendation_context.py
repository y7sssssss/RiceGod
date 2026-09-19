import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from ricegod.application.agents import planner, recipe_retrieval, knowledge_retrieval, conversation
from ricegod.config import settings
from ricegod.application.agents import router, workflow
from ricegod.application.agents.recommendations import is_recipe_selection, select_recommendation
from ricegod.application.agents.state import (
    AgentState, PlannedTask, QueryPlan, RecommendationGroup, Router, TaskResult,
)
from ricegod.domain.recipe_ranking import RecipeRequest
from ricegod.domain.recipes import RecipeConstraints
from ricegod.infrastructure.recipe_graph_repository import RecipeGraphRepository
from ricegod.infrastructure.recipe_repository import RecipeRepository


def _row(name, ingredient="鸡蛋", method="炒", time="十分钟"):
    return {"name": name, "主食材": [[ingredient, "适量"]], "辅料": [], "口味": "清淡",
            "工艺": method, "耗时": time, "类型": "家常菜", "做法": "1:准备材料。2:加热至熟。"}


@pytest.fixture
def graph(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", None)
    rows = [_row("鸡蛋快炒"), _row("鸡蛋蒸羹", method="蒸"), _row("鸡蛋煎饼", method="煎", time="廿分钟"),
            _row("土豆快炒", "土豆"), _row("土豆蒸泥", "土豆", "蒸"), _row("土豆煎饼", "土豆", "煎", "廿分钟")]
    path = tmp_path / "recipes.json"
    path.write_text(json.dumps({row["name"]: row for row in rows}, ensure_ascii=False), encoding="utf-8")
    catalog = RecipeGraphRepository(data_path=path)
    local = RecipeRepository(str(path))
    return workflow.build_graph(router.analyze_intent, conversation.general_query, conversation.additional_query,
                                router._build_llm, catalog, local)


async def _turn(graph, message, thread="continuity", **inputs):
    return await graph.ainvoke({"messages": [HumanMessage(content=message)], **inputs},
                              {"configurable": {"thread_id": thread}})


def _names(groups):
    return [[row.get("菜名") or row["name"] for row in group.records] for group in groups]


def _selected(result):
    return [row.get("菜名") or row["name"] for task in result["task_results"].values()
            if task.route == "recipe_query" for row in task.records]


def test_thanks_details_and_another_selection_keep_original_recommendation_order(graph):
    async def scenario():
        initial = await _turn(graph, "用鸡蛋推荐三道菜")
        original = _names(initial["last_recommendations"])
        assert len(original[0]) == 3
        thanks = await _turn(graph, "谢谢")
        assert _names(thanks["last_recommendations"]) == original
        assert all(result.route == "general_query" for result in thanks["task_results"].values())
        second = await _turn(graph, "第二道菜怎么做？")
        assert _selected(second) == [original[0][1]]
        assert _names(second["last_recommendations"]) == original
        assert not second["plan"].recipe_request.recommendation
        third = await _turn(graph, "再给我第三道菜的材料和步骤。")
        assert _selected(third) == [original[0][2]]
        assert _names(third["last_recommendations"]) == original
        assert len(third["task_results"]) == 1
        snapshot = graph.get_state({"configurable": {"thread_id": "continuity"}}).values
        assert isinstance(snapshot["last_recommendations"][0], RecommendationGroup)
        assert _names(snapshot["last_recommendations"]) == original
    asyncio.run(scenario())


def test_successful_culture_query_preserves_recommendations(graph, monkeypatch):
    async def culture(task, upstream):
        return TaskResult(task_id=task.id, route=task.route, question=task.question,
                          content="文化证据", records=[{"text": "文化证据"}], sources=["culture-source"])
    monkeypatch.setattr(knowledge_retrieval, "retrieve_knowledge", culture)
    async def scenario():
        initial = await _turn(graph, "用鸡蛋推荐三道菜")
        original = _names(initial["last_recommendations"])
        culture_result = await _turn(graph, "川菜有什么历史？")
        assert culture_result["sources"] == ["culture-source"]
        assert _names(culture_result["last_recommendations"]) == original
        selected = await _turn(graph, "第二道菜怎么做")
        assert _selected(selected) == [original[0][1]]
    asyncio.run(scenario())


def test_new_recommendation_replaces_list_but_named_details_do_not(graph):
    async def scenario():
        await _turn(graph, "用鸡蛋推荐三道菜")
        detail = await _turn(graph, "土豆快炒怎么做")
        assert all("鸡蛋" in name for name in _names(detail["last_recommendations"])[0])
        new = await _turn(graph, "改用土豆推荐三道菜")
        names = _names(new["last_recommendations"])
        assert len(names[0]) == 3 and all("土豆" in name for name in names[0])
        selected = await _turn(graph, "第二道菜怎么做")
        assert _selected(selected) == [names[0][1]]
    asyncio.run(scenario())


def test_group_selection_after_thanks_and_repeated_details(graph):
    async def scenario():
        initial = await _turn(graph, "早餐用鸡蛋推荐两道菜，晚餐用土豆推荐三道菜")
        groups = initial["last_recommendations"]
        assert [group.label for group in groups] == ["早餐", "晚餐"]
        original = _names(groups)
        assert [len(group.records) for group in groups] == [2, 3]
        await _turn(graph, "谢谢")
        ambiguous = await _turn(graph, "第二道菜怎么做")
        assert "哪一组" in ambiguous["answer"] and ambiguous["sources"] == []
        assert _names(ambiguous["last_recommendations"]) == original
        dinner = await _turn(graph, "晚饭第二道菜怎么做")
        assert _selected(dinner) == [original[1][1]]
        breakfast = await _turn(graph, "早饭第一道菜怎么做")
        assert _selected(breakfast) == [original[0][0]]
        group_number = await _turn(graph, "第二组的第三道菜")
        assert _selected(group_number) == [original[1][2]]
        assert _names(group_number["last_recommendations"]) == original
    asyncio.run(scenario())


@pytest.mark.parametrize("query", ["第零道菜怎么做", "第0道菜怎么做", "第4道菜怎么做", "第十道菜怎么做"])
def test_out_of_range_selection_clarifies_and_keeps_list(graph, query):
    async def scenario():
        initial = await _turn(graph, "用鸡蛋推荐三道菜")
        result = await _turn(graph, query)
        assert "只有 3 道菜" in result["answer"]
        assert result["sources"] == [] and _selected(result) == []
        assert _names(result["last_recommendations"]) == _names(initial["last_recommendations"])
    asyncio.run(scenario())


def test_no_list_selection_never_calls_recipe_backend(graph, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Unresolved reference must not query arbitrary recipes")
    monkeypatch.setattr(RecipeRepository, "search_recipes", forbidden)
    result = asyncio.run(_turn(graph, "第二道菜怎么做"))
    assert "还没有可选择的推荐列表" in result["answer"]
    assert result["sources"] == [] and not result["last_recommendations"]


def test_empty_recommendation_and_new_restriction_do_not_replace_previous_list(graph):
    async def scenario():
        initial = await _turn(graph, "用鸡蛋推荐三道菜")
        original = _names(initial["last_recommendations"])
        empty = await _turn(graph, "用鸡蛋和土豆推荐三道菜")
        assert _selected(empty) == []
        assert _names(empty["last_recommendations"]) == original
        blocked = await _turn(graph, "第二道菜怎么做，不要鸡蛋")
        assert _selected(blocked) == []  # Current exclusions are still enforced.
        assert _names(blocked["last_recommendations"]) == original
    asyncio.run(scenario())


def test_recommendation_context_is_isolated_between_users_and_conversations(graph):
    async def scenario():
        first_thread = json.dumps(["user-a", "conversation-a"])
        initial = await _turn(graph, "用鸡蛋推荐三道菜", first_thread)
        for other in [json.dumps(["user-b", "conversation-a"]), json.dumps(["user-a", "conversation-b"])]:
            result = await _turn(graph, "第二道菜怎么做", other)
            assert "还没有可选择的推荐列表" in result["answer"]
            assert not result["last_recommendations"]
        result = await _turn(graph, "第二道菜怎么做", first_thread)
        assert _selected(result) == [_names(initial["last_recommendations"])[0][1]]
    asyncio.run(scenario())


@pytest.mark.parametrize("question,expected", [
    ("第二道", True), ("我选第二道菜", True), ("晚餐第二道菜怎么做", True),
    ("第二组的第一道菜", True), ("第2个问题怎么做", False), ("第2个步骤怎么做", False),
])
def test_selection_detection_accepts_recipe_choices_and_ignores_other_ordinals(question, expected):
    assert is_recipe_selection(question) == expected


def test_selection_does_not_depend_on_llm_routing_or_planning(graph, monkeypatch, catalog):
    initial = asyncio.run(_turn(graph, "用鸡蛋推荐三道菜"))
    monkeypatch.setattr(settings, "LLM_API_KEY", "test")
    def forbidden(**kwargs):
        raise AssertionError("Ordinal routing/planning should use the stored snapshot")
    monkeypatch.setattr(router, "_build_llm", forbidden)
    state = AgentState(messages=[HumanMessage(content="第二道菜怎么做")], router=Router(type=["recipe_query"]),
                       last_recommendations=initial["last_recommendations"])
    intent = asyncio.run(router.analyze_intent(state))
    assert intent["router"].type == ["recipe_query"]
    plan = asyncio.run(planner.make_plan(state, catalog, forbidden))
    assert plan.selected_recipe_name == _names(state.last_recommendations)[0][1]
    assert plan.recipe_request.limit == 1 and not plan.recipe_request.recommendation


@pytest.mark.parametrize("answer", ["菜谱乙，菜谱甲", "菜谱甲", "已记录推荐：菜谱甲、菜谱乙。"])
def test_snapshot_matches_displayed_order_when_llm_omits_or_reorders_recipes(graph, monkeypatch, answer, catalog):
    monkeypatch.setattr(settings, "LLM_API_KEY", "test")
    async def plan(*args):
        return QueryPlan(tasks=[PlannedTask(id="recommend", route="recipe_query", question="推荐两道菜",
                                          recipe_request=RecipeRequest(query="推荐两道菜", recommendation=True, limit=2))])
    async def evidence(task, *args):
        return TaskResult(task_id=task.id, route=task.route, question=task.question,
                          records=[_row("菜谱甲"), _row("菜谱乙")], content="unused free text")
    class Model:
        async def ainvoke(self, messages):
            return AIMessage(content=answer)
    monkeypatch.setattr(planner, "make_plan", plan)
    monkeypatch.setattr(recipe_retrieval, "retrieve_recipe", evidence)
    graph = workflow.build_graph(lambda state: {"router": Router(type=["recipe_query"])}, None, None,
                                lambda **kwargs: Model(), catalog, None)
    result = asyncio.run(_turn(graph, "推荐两道菜"))
    assert result["answer"].index("菜谱甲") < result["answer"].index("菜谱乙")
    assert _names(result["last_recommendations"]) == [["菜谱甲", "菜谱乙"]]


def test_dependency_completion_order_does_not_change_display_group_order(graph, monkeypatch):
    async def plan(*args):
        return QueryPlan(tasks=[PlannedTask(id=task_id, route="recipe_query", question="推荐一道菜",
            depends_on=["a"] if task_id == "b" else [],
            recipe_request=RecipeRequest(query="推荐一道菜", recommendation=True, limit=1)) for task_id in ("a", "b", "c")])
    async def evidence(task, *args):
        return TaskResult(task_id=task.id, route=task.route, question=task.question,
                          records=[_row("菜谱" + task.id)], content="evidence")
    monkeypatch.setattr(planner, "make_plan", plan)
    monkeypatch.setattr(recipe_retrieval, "retrieve_recipe", evidence)
    result = asyncio.run(_turn(graph, "推荐三道菜"))
    assert list(result["task_results"]) == ["a", "c", "b"]
    assert _names(result["last_recommendations"]) == [["菜谱a"], ["菜谱b"], ["菜谱c"]]
    assert select_recommendation("第二组第一道菜怎么做", result["last_recommendations"]).name == "菜谱b"


def test_missing_and_duplicate_group_labels_require_an_explicit_group_number():
    groups = [RecommendationGroup(task_id="a", label="早餐（第1组）", records=[_row("鸡蛋蒸羹")]),
              RecommendationGroup(task_id="b", label="早餐（第2组）", records=[_row("鸡蛋煎饼")])]
    assert select_recommendation("早餐第一道菜怎么做", groups).error_code == "recipe_selection_ambiguous_group"
    assert select_recommendation("晚餐第一道菜怎么做", groups).error_code == "recipe_selection_missing_group"
    assert select_recommendation("第二组第一道菜怎么做", groups).name == "鸡蛋煎饼"


def test_empty_group_and_multiple_choices_do_not_silently_select_another_recipe():
    groups = [RecommendationGroup(task_id="a", label="早餐", records=[]),
              RecommendationGroup(task_id="b", label="晚餐", records=[_row("土豆快炒", "土豆")])]
    assert select_recommendation("早餐第一道菜怎么做", groups).error_code == "recipe_selection_empty_group"
    assert select_recommendation("第二道和第三道菜怎么做", groups).error_code == "recipe_selection_multiple"
