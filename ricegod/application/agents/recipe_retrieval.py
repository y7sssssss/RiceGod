"""Retrieve recipe evidence, validate Cypher, and fall back to local data."""

import asyncio
import logging
from typing import Any, Callable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from ricegod.application.agents.state import PlannedTask, QueryPlan, TaskResult
from ricegod.application.agents.recipe_formatting import format_recipe_evidence
from ricegod.config import settings
from ricegod.infrastructure.recipe_graph_repository import RecipeGraphRepository
from ricegod.infrastructure.recipe_repository import RecipeRepository
from ricegod.domain.recipe_ranking import RecipeRequest, build_recipe_request, rank_recipes

logger = logging.getLogger(__name__)


class GeneratedCypher(BaseModel):
    cypher: str
    logic: str = ""


async def retrieve_recipe(
    task: PlannedTask,
    plan: QueryPlan,
    graph_repository: RecipeGraphRepository,
    local_repository: RecipeRepository,
    model_factory: Callable,
) -> TaskResult:
    if plan.recipe_selection_error:
        return TaskResult(task_id=task.id, route=task.route, question=task.question,
                          status="blocked", content=plan.recipe_selection_error,
                          error_code="recipe_selection_unresolved")
    filters = task.constraints if task.constraints is not None else plan.constraints
    request = task.recipe_request or (plan.recipe_request if plan.recipe_request.query
                                     else build_recipe_request(task.question, filters))
    limit = request.limit
    # Structured filters always take the composed template path, never arbitrary generated Cypher.
    has_filters = any(value for value in filters.model_dump().values())
    graph_operation = request.operation in ("similar", "substitute")
    if not settings.LLM_API_KEY and not graph_operation:
        rows = await asyncio.to_thread(local_repository.search_recipes, task.question,
            limit=limit, constraints=filters, request=request)
        return _local_result(task, rows, "llm_unconfigured", request=request)
    try:
        if request.operation == "similar":
            if not filters.dish_name:
                return _operation_target_missing(task, "similar_recipe_missing_dish",
                                                 "请明确要查找哪道菜的相似菜。")
            cypher_request = graph_repository.build_similar_recipe_cypher(filters.dish_name, limit)
            rows = await asyncio.to_thread(graph_repository.query_by_cypher,
                                           cypher_request.cypher, cypher_request.params)
        elif request.operation == "substitute":
            if not filters.required_ingredients:
                return _operation_target_missing(task, "substitution_missing_ingredient",
                                                 "请明确需要替代的食材。")
            cypher_request = graph_repository.build_substitution_cypher(
                filters.required_ingredients[0], limit)
            rows = await asyncio.to_thread(graph_repository.query_by_cypher,
                                           cypher_request.cypher, cypher_request.params)
        elif request.recommendation:
            candidates = await asyncio.to_thread(graph_repository.query_ranked_candidates, filters, request)
            rows = rank_recipes(candidates, filters, request)
        elif has_filters:
            cypher_request = graph_repository.build_constrained_cypher(filters, limit)
            cypher, params = cypher_request.cypher, cypher_request.params
        else:
            cypher_request = graph_repository.build_predefined_cypher(task.question)
            if cypher_request:
                cypher, params = cypher_request.cypher, cypher_request.params
            else:
                cypher = await _generate_cypher(task.question, graph_repository, model_factory)
                params = {}
        if not graph_operation and not request.recommendation:
            rows = await asyncio.to_thread(graph_repository.query_by_cypher, cypher, params)
            rows = [row for row in rows if not has_filters or filters.matches(row)][:limit]
        sources = _graph_sources(rows, request)
        return TaskResult(task_id=task.id, route=task.route, question=task.question,
                          status="ok" if rows else "empty", records=rows,
                          content=graph_repository.format_records(rows) if graph_operation
                          else format_recipe_evidence(rows, request) if request.recommendation
                          else graph_repository.format_records(rows), sources=sources)
    except Exception:
        if graph_operation:
            logger.exception("Graph-specific recipe retrieval failed")
            return TaskResult(task_id=task.id, route=task.route, question=task.question,
                              status="failed", content="图谱查询暂时不可用。",
                              error_code="recipe_graph_unavailable")
        logger.exception("Recipe graph retrieval failed; using local evidence")
        rows = await asyncio.to_thread(local_repository.search_recipes, task.question,
            limit=limit, constraints=filters, request=request)
        return _local_result(task, rows, "recipe_graph_unavailable", request=request)


def _operation_target_missing(task: PlannedTask, code: str, message: str) -> TaskResult:
    return TaskResult(task_id=task.id, route=task.route, question=task.question,
                      status="blocked", content=message, error_code=code)


def _graph_sources(rows: list[dict[str, Any]], request: RecipeRequest) -> list[str]:
    if request.operation == "substitute":
        return list(dict.fromkeys(
            f"neo4j:Ingredient:{row['原食材']}:CAN_SUBSTITUTE:{row['替代食材']}"
            for row in rows if row.get("原食材") and row.get("替代食材")
        ))
    return [f"neo4j:Dish:{row['菜名']}" for row in rows if row.get("菜名")]


def _local_result(task: PlannedTask, rows: list[dict[str, Any]], reason: str, *,
                  request: RecipeRequest | None = None) -> TaskResult:
    return TaskResult(task_id=task.id, route=task.route, question=task.question,
                      status="degraded" if rows else "empty", records=rows,
                      content=format_recipe_evidence(rows, request) if request else RecipeRepository.format_recipe_context(rows),
                      sources=[f"local:recipe.json:{row['name']}" for row in rows], error_code=reason)


async def _generate_cypher(question: str, repository: RecipeGraphRepository, model_factory: Callable) -> str:
    from ricegod.application.agents.prompts import TEXT2CYPHER_SYSTEM_PROMPT

    messages = [SystemMessage(content=TEXT2CYPHER_SYSTEM_PROMPT.format(schema=repository.schema_text)),
                HumanMessage(content=question)]
    for attempt in range(settings.CYPHER_MAX_ATTEMPTS):
        result = await model_factory(temperature=0).with_structured_output(GeneratedCypher).ainvoke(messages)
        result = GeneratedCypher.model_validate(result)
        cypher = repository.clean_cypher(result.cypher)
        try:
            repository.validate_read_only_cypher(cypher)
            return cypher
        except ValueError as exc:
            if attempt + 1 == settings.CYPHER_MAX_ATTEMPTS:
                raise
            messages.extend([AIMessage(content=cypher),
                             HumanMessage(content=f"校验失败：{exc}。请重新生成只读查询。")])
    raise ValueError("No validated Cypher produced")
