import asyncio
import os

import pytest

from ricegod.application.agents.recipe_retrieval import retrieve_recipe
from ricegod.application.agents.state import PlannedTask, QueryPlan, RecipeOperationSpec
from ricegod.config import settings
from ricegod.domain.recipe_ranking import RecipeRequest
from ricegod.infrastructure.recipe_graph_repository import RecipeGraphRepository
from ricegod.infrastructure.recipe_repository import RecipeRepository


pytestmark = pytest.mark.skipif(
    os.getenv("RICEGOD_TEST_NEO4J") != "1",
    reason="Opt-in real Neo4j integration",
)


def test_live_neo4j_template_query_without_llm_key(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", None)
    graph = RecipeGraphRepository()
    try:
        rows = graph.query_by_cypher(
            "MATCH (dish:Dish) WHERE dish.name IN $names RETURN dish.name AS 菜名 ORDER BY 菜名 LIMIT 1",
            {"names": graph.recipe_names},
        )
        assert rows, "Neo4j has no Dish shared with the local recipe catalog."
        dish_name = rows[0]["菜名"]
        task = PlannedTask(id="live-recipe", route="recipe_query", question=f"{dish_name}怎么做")

        result = asyncio.run(retrieve_recipe(
            task,
            QueryPlan(),
            graph,
            RecipeRepository(),
            model_factory=None,
        ))

        assert result.status == "ok"
        assert result.records[0]["name"]
        assert result.sources == [f"neo4j:Dish:{result.records[0]['name']}"]
    finally:
        graph.client.close()


def test_live_neo4j_search_similar_and_substitute_operations(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_KEY", None)
    graph = RecipeGraphRepository()
    local = RecipeRepository()
    try:
        search_task = PlannedTask(
            id="search",
            route="recipe_query",
            question="用鸡蛋推荐一道菜",
            constraints=graph.extract_constraints("用鸡蛋推荐一道菜"),
            recipe_request=RecipeRequest(
                query="用鸡蛋推荐一道菜", recommendation=True, limit=1, requested_count=1),
        )
        search = asyncio.run(retrieve_recipe(
            search_task, QueryPlan(tasks=[search_task]), graph, local, model_factory=None))

        similar_seed = graph.query_by_cypher("""
MATCH (source:Dish)-[:HAS_MAIN_INGREDIENT|HAS_AUX_INGREDIENT]->(i:Ingredient)
MATCH (candidate:Dish)-[:HAS_MAIN_INGREDIENT|HAS_AUX_INGREDIENT]->(i)
WHERE source <> candidate
RETURN source.name AS name LIMIT 1
""", {})[0]["name"]
        similar_task = PlannedTask(
            id="similar",
            route="recipe_query",
            question=f"找和{similar_seed}相似的菜",
            operation_spec=RecipeOperationSpec(operation="similar", dish_name=similar_seed),
            recipe_request=RecipeRequest(
                query=f"找和{similar_seed}相似的菜", operation="similar", limit=2),
        )
        similar = asyncio.run(retrieve_recipe(
            similar_task, QueryPlan(tasks=[similar_task]), graph, local, model_factory=None))

        substitution_seed = graph.query_by_cypher("""
MATCH (ingredient:Ingredient)-[:CAN_SUBSTITUTE]->(replacement:Ingredient)
RETURN ingredient.name AS ingredient, replacement.name AS replacement LIMIT 1
""", {})[0]
        substitute_task = PlannedTask(
            id="substitute",
            route="recipe_query",
            question=f"用{substitution_seed['replacement']}代替{substitution_seed['ingredient']}",
            operation_spec=RecipeOperationSpec(
                operation="substitute",
                ingredient=substitution_seed["ingredient"],
                replacement=substitution_seed["replacement"],
            ),
            recipe_request=RecipeRequest(operation="substitute", limit=2),
        )
        substitute = asyncio.run(retrieve_recipe(
            substitute_task, QueryPlan(tasks=[substitute_task]), graph, local, model_factory=None))

        assert search.status == "ok" and search.records
        assert similar.status == "ok" and similar.records
        assert substitute.status == "ok" and substitute.records
        assert substitute.records[0]["替代食材"] == substitution_seed["replacement"]
    finally:
        graph.client.close()
