import asyncio

from langchain_core.messages import AIMessage, HumanMessage

from ricegod.application.agents import planner, recipe_retrieval, knowledge_retrieval, conversation
from ricegod.config import settings
from ricegod.application.agents import router, workflow
from ricegod.application.agents.state import AgentState, PlannedTask, QueryPlan, Router, TaskResult


def _graph(catalog, local, routes):
    async def intent(state):
        return {"router": Router(type=routes)}
    return workflow.build_graph(intent, conversation.general_query, conversation.additional_query, router._build_llm,
                                catalog, local)


def test_graph_fans_out_independent_tasks_and_merges_sources(monkeypatch, catalog, local):
    monkeypatch.setattr(settings, "LLM_API_KEY", None)

    async def scenario():
        entered = set()
        barrier = asyncio.Event()

        async def evidence(task, *args):
            entered.add(task.route)
            if len(entered) == 2:
                barrier.set()
            # Sequential execution would time out: both tasks must start before either finishes.
            await asyncio.wait_for(barrier.wait(), 1)
            return TaskResult(task_id=task.id, route=task.route, question=task.question,
                              content=task.route, sources=["shared", task.route])

        monkeypatch.setattr(recipe_retrieval, "retrieve_recipe", evidence)
        monkeypatch.setattr(knowledge_retrieval, "retrieve_knowledge", evidence)
        result = await _graph(catalog, local, ["recipe_query", "food_knowledge"]).ainvoke(
            {"messages": [HumanMessage(content="推荐一道菜并介绍川菜历史")]},
            {"configurable": {"thread_id": "parallel"}},
        )
        assert entered == {"recipe_query", "food_knowledge"}
        assert len(result["task_results"]) == 2
        assert result["sources"] == ["shared", "recipe_query", "food_knowledge"]
        assert all(item.status == "ok" for item in result["task_results"].values())
        assert len(result["messages"]) == 2  # No intermediate assistant answer is checkpointed.

    asyncio.run(scenario())


def test_dependent_culture_task_receives_real_recipe_records(monkeypatch, catalog, local):
    monkeypatch.setattr(settings, "LLM_API_KEY", None)
    order = []

    async def recipe(task, *args):
        order.append("recipe")
        return TaskResult(task_id=task.id, route=task.route, question=task.question,
                          records=[{"name": "香肠炒菜干"}], content="recipe evidence")

    async def culture(task, upstream):
        order.append("culture")
        assert upstream[0].records == [{"name": "香肠炒菜干"}]
        return TaskResult(task_id=task.id, route=task.route, question=task.question, content="culture evidence")

    monkeypatch.setattr(recipe_retrieval, "retrieve_recipe", recipe)
    monkeypatch.setattr(knowledge_retrieval, "retrieve_knowledge", culture)
    result = asyncio.run(_graph(catalog, local, ["food_knowledge", "recipe_query"]).ainvoke(
        {"messages": [HumanMessage(content="推荐一道菜并介绍它的来历")]},
        {"configurable": {"thread_id": "dependency"}},
    ))
    assert order == ["recipe", "culture"]
    assert len(result["task_results"]) == 2


def test_failure_in_one_task_keeps_other_task_evidence(monkeypatch, catalog, local):
    monkeypatch.setattr(settings, "LLM_API_KEY", None)

    async def recipe(task, *args):
        return TaskResult(task_id=task.id, route=task.route, question=task.question,
                          content="可靠菜谱证据", sources=["recipe-source"])

    async def culture(*args):
        raise RuntimeError("private infrastructure error")

    monkeypatch.setattr(recipe_retrieval, "retrieve_recipe", recipe)
    monkeypatch.setattr(knowledge_retrieval, "retrieve_knowledge", culture)
    result = asyncio.run(_graph(catalog, local, ["recipe_query", "food_knowledge"]).ainvoke(
        {"messages": [HumanMessage(content="推荐菜并介绍川菜历史")]},
        {"configurable": {"thread_id": "failure"}},
    ))
    assert "可靠菜谱证据" in result["answer"]
    assert "private infrastructure error" not in result["answer"]
    assert result["sources"] == ["recipe-source"]
    assert result["task_results"]["task_1"].status == "failed"


def test_task_timeout_is_bounded_and_reported(monkeypatch, catalog, local):
    monkeypatch.setattr(settings, "LLM_API_KEY", None)
    monkeypatch.setattr(settings, "AGENT_TASK_TIMEOUT_SECONDS", 0.02)

    async def hanging(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr(recipe_retrieval, "retrieve_recipe", hanging)
    result = asyncio.run(_graph(catalog, local, ["recipe_query"]).ainvoke(
        {"messages": [HumanMessage(content="推荐菜")]},
        {"configurable": {"thread_id": "timeout"}},
    ))
    assert result["task_results"]["task_0"].error_code == "task_timeout"


def test_similar_and_substitute_operations_reach_graph_templates(monkeypatch, catalog, local):
    monkeypatch.setattr(settings, "LLM_API_KEY", None)
    calls = []

    def query_by_cypher(cypher, params):
        calls.append((cypher, params))
        if "CAN_SUBSTITUTE" in cypher:
            return [{"原食材": "玉米淀粉", "替代食材": "马铃薯淀粉", "使用场景": "勾芡", "置信度": 0.8}]
        return [{"菜名": "家庭快手肠粉", "similarity_score": 10,
                 "shared_main_count": 1, "shared_ingredients": ["香肠"]}]

    monkeypatch.setattr(catalog, "query_by_cypher", query_by_cypher)
    graph = _graph(catalog, local, ["recipe_query"])

    similar = asyncio.run(graph.ainvoke(
        {"messages": [HumanMessage(content="找一道和香肠炒菜干相似的菜")]},
        {"configurable": {"thread_id": "graph-operation-similar"}},
    ))
    substitute = asyncio.run(graph.ainvoke(
        {"messages": [HumanMessage(content="玉米淀粉可以用什么替代")]},
        {"configurable": {"thread_id": "graph-operation-substitute"}},
    ))

    assert similar["plan"].tasks[0].recipe_request.operation == "similar"
    assert similar["task_results"]["task_0"].records[0]["菜名"] == "家庭快手肠粉"
    assert substitute["plan"].tasks[0].recipe_request.operation == "substitute"
    assert substitute["task_results"]["task_0"].records[0]["替代食材"] == "马铃薯淀粉"
    assert "shared_ingredients" in calls[0][0]
    assert "CAN_SUBSTITUTE" in calls[1][0]


def test_next_turn_replaces_transient_results_and_sources(monkeypatch, catalog, local):
    monkeypatch.setattr(settings, "LLM_API_KEY", None)

    async def recipe(task, *args):
        return TaskResult(task_id=task.id, route=task.route, question=task.question,
                          content=task.question, sources=[task.question])

    monkeypatch.setattr(recipe_retrieval, "retrieve_recipe", recipe)

    async def scenario():
        graph = _graph(catalog, local, ["recipe_query"])
        config = {"configurable": {"thread_id": "two-turns"}}
        await graph.ainvoke({"messages": [HumanMessage(content="第一轮推荐菜")]}, config)
        result = await graph.ainvoke({"messages": [HumanMessage(content="第二轮推荐菜")]}, config)
        assert len(result["task_results"]) == 1
        assert result["sources"] == ["第二轮推荐菜"]
        assert "第一轮" not in result["answer"]
        assert len(result["messages"]) == 4

    asyncio.run(scenario())


def test_invalid_dependency_plan_falls_back_and_preserves_exclusions(monkeypatch, catalog):
    monkeypatch.setattr(settings, "LLM_API_KEY", "test")

    class Model:
        def with_structured_output(self, schema):
            return self

        async def ainvoke(self, messages):
            return QueryPlan(tasks=[PlannedTask(id="cycle", route="recipe_query", question="推荐菜",
                                               depends_on=["cycle"])])

    state = AgentState(messages=[HumanMessage(content="不要香菜，推荐一道菜，并介绍川菜的历史")],
                       router=Router(type=["recipe_query", "food_knowledge"]))
    plan = asyncio.run(planner.make_plan(state, catalog, lambda **kwargs: Model()))
    assert plan.tasks[0].id == "task_0"
    assert "香菜" in plan.constraints.excluded_ingredients


def test_empty_upstream_blocks_dependent_lookup(monkeypatch):
    task = PlannedTask(id="culture", route="food_knowledge", question="介绍它的来历", depends_on=["recipe"])
    upstream = [TaskResult(task_id="recipe", route="recipe_query", question="推荐菜", status="empty")]
    result = asyncio.run(knowledge_retrieval.retrieve_knowledge(task, upstream))
    assert result.status == "blocked"
    assert result.error_code == "missing_recipe_evidence"


def test_invalid_cypher_is_revalidated_before_any_database_execution(monkeypatch, catalog, local):
    monkeypatch.setattr(settings, "LLM_API_KEY", "test")
    monkeypatch.setattr(settings, "CYPHER_MAX_ATTEMPTS", 2)
    executed = []

    class Repository:
        schema_text = "Dish"
        clean_cypher = staticmethod(catalog.clean_cypher)
        validate_read_only_cypher = staticmethod(catalog.validate_read_only_cypher)
        format_records = staticmethod(catalog.format_records)

        def build_predefined_cypher(self, question):
            return None

        def query_by_cypher(self, cypher, params):
            executed.append(cypher)
            return [{"菜名": "fixture"}]

    class Model:
        def __init__(self):
            self.calls = 0

        def with_structured_output(self, schema):
            return self

        async def ainvoke(self, messages):
            self.calls += 1
            return recipe_retrieval.GeneratedCypher(cypher="MATCH (n) DELETE n" if self.calls == 1 else "RETURN 1")

    model = Model()
    task = PlannedTask(id="recipe", route="recipe_query", question="fixture")
    result = asyncio.run(recipe_retrieval.retrieve_recipe(task, QueryPlan(), Repository(),
                                                local, lambda **kwargs: model))
    assert model.calls == 2
    assert executed == ["RETURN 1"]
    assert result.status == "ok"


def test_grounded_synthesis_uses_evidence_and_returns_actual_sources(monkeypatch, catalog, local):
    monkeypatch.setattr(settings, "LLM_API_KEY", "test")
    captured = []

    async def plan(state, *args):
        return QueryPlan(tasks=[PlannedTask(id="recipe", route="recipe_query", question="推荐一道菜")])

    async def recipe(task, *args):
        return TaskResult(task_id=task.id, route=task.route, question=task.question,
                          records=[{"name": "fixture-dish"}], content="retrieved evidence", sources=["fixture-source"])

    class Model:
        async def ainvoke(self, messages):
            captured.extend(messages)
            return AIMessage(content="fixture-dish：最终回答")

    monkeypatch.setattr(planner, "make_plan", plan)
    monkeypatch.setattr(recipe_retrieval, "retrieve_recipe", recipe)
    graph = workflow.build_graph(lambda state: {"router": Router(type=["recipe_query"])},
                                conversation.general_query, conversation.additional_query, lambda **kwargs: Model(),
                                catalog, local)
    result = asyncio.run(graph.ainvoke({"messages": [HumanMessage(content="推荐一道菜")]},
                                      {"configurable": {"thread_id": "synthesis"}}))
    assert "fixture-dish" in captured[-1].content
    assert "fixture-source" in captured[-1].content
    assert result["answer"] == "fixture-dish：最终回答"
    assert result["sources"] == ["fixture-source"]
