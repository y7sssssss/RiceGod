"""Classify, plan, validate, and ground tasks in one decision stage."""

import json
import logging
from typing import Callable

from ricegod.application.agents.context import build_model_context
from ricegod.application.agents.planning_policy import (
    DEPENDENT_RECIPE_WORDS,
    deterministic_plan_reason,
    deterministic_routes,
    ingredient_constraint_conflict,
)
from ricegod.application.agents.prompts import PLANNER_PROMPT
from ricegod.application.agents.recommendations import is_recipe_selection, select_recommendation
from ricegod.application.agents.state import AgentState, PlannedTask, QueryPlan, Router
from ricegod.application.agents.task_conditions import ground_task_conditions, meal_scopes
from ricegod.application.performance import record_event
from ricegod.config import settings
from ricegod.infrastructure.recipe_graph_repository import RecipeGraphRepository

logger = logging.getLogger(__name__)


def _question(state: AgentState) -> str:
    return state.question or (str(state.messages[-1].content) if state.messages else "")


def _fallback_tasks(question: str, routes: list[str]) -> list[PlannedTask]:
    tasks = [PlannedTask(id=f"task_{index}", route=route, question=question,
                         source_text=question if route == "recipe_query" else "")
             for index, route in enumerate(routes)]
    scopes = meal_scopes(question)
    if scopes and "recipe_query" in routes and len(scopes) + len(tasks) - 1 <= 4:
        tasks = [task for task in tasks if task.route != "recipe_query"] + [
            PlannedTask(id=f"meal_{index}", route="recipe_query", question=scope, source_text=scope)
            for index, scope in enumerate(scopes)]
    recipes = [task for task in tasks if task.route == "recipe_query"]
    if recipes and any(word in question for word in DEPENDENT_RECIPE_WORDS):
        for task in tasks:
            if task.route == "food_knowledge":
                task.depends_on = [recipe.id for recipe in recipes]
        tasks.sort(key=lambda task: bool(task.depends_on))
    return tasks


def _validate_plan(plan: QueryPlan) -> None:
    seen: set[str] = set()
    for task in plan.tasks:
        if not task.id or task.id in seen or not task.question.strip():
            raise ValueError("Invalid task identity or question")
        if any(dependency not in seen for dependency in task.depends_on):
            raise ValueError("Dependencies must reference earlier tasks")
        if task.route == "recipe_query" and not task.source_text:
            task.source_text = task.question
        seen.add(task.id)
    if not plan.tasks:
        raise ValueError("Plan must contain at least one task")


def router_summary(plan: QueryPlan, question: str) -> Router:
    routes = list(dict.fromkeys(task.route for task in plan.tasks)) or ["additional_query"]
    return Router(type=routes, question=question,
                  logic=f"task planner ({plan.decision_source}): " + ", ".join(routes))


async def make_plan(state: AgentState, catalog: RecipeGraphRepository,
                    model_factory: Callable | None) -> QueryPlan:
    question = _question(state)
    routes = deterministic_routes(question, catalog)
    seed = catalog.extract_constraints(question, state.user_memories)
    conflict = ingredient_constraint_conflict(question, catalog)

    if is_recipe_selection(question):
        record_event("planner", mode="selection")
        selection = select_recommendation(question, state.last_recommendations)
        plan = QueryPlan(tasks=_fallback_tasks(question, ["recipe_query"]),
                         constraints=seed, decision_source="selection")
        plan = ground_task_conditions(plan, question, catalog, state.user_memories, selection.name)
        recipes = [task for task in plan.tasks if task.route == "recipe_query"]
        for task in recipes:
            task.recipe_request = task.recipe_request.model_copy(update={
                "recommendation": False, "limit": 1, "requested_count": None, "detailed": True})
        plan.selected_recipe_name = selection.name
        plan.recipe_selection_error = selection.clarification or None
        if recipes:
            plan.recipe_request = recipes[0].recipe_request
        return plan

    reason = deterministic_plan_reason(question, routes or [], meal_scopes(question), catalog)
    if routes and reason:
        record_event("planner", mode="rules", reason=reason)
        plan = QueryPlan(tasks=_fallback_tasks(question, routes), constraints=seed,
                         decision_source="rules")
        if conflict:
            plan.clarification = (
                f"你的要求有矛盾：既要求使用{conflict}，又要求排除{conflict}。"
                f"请问你想保留{conflict}，还是不要{conflict}？"
            )
        return ground_task_conditions(plan, question, catalog, state.user_memories)

    if not settings.LLM_API_KEY or model_factory is None:
        fallback_routes = routes or ["additional_query"]
        record_event("planner", mode="fallback", phase="configuration", reason="llm_unconfigured")
        plan = QueryPlan(tasks=_fallback_tasks(question, fallback_routes), constraints=seed,
                         decision_source="fallback")
        return ground_task_conditions(plan, question, catalog, state.user_memories)

    suggested = _fallback_tasks(question, routes) if routes else []
    phase = "context"
    try:
        messages = build_model_context(state, "planner", system_prompt=PLANNER_PROMPT,
            evidence=json.dumps({
                "question": question,
                "user_memories": state.user_memories,
                "known_constraints": seed.model_dump(),
                "suggested_routes": routes or [],
                "suggested_tasks": [task.model_dump(include={
                    "id", "route", "question", "source_text", "depends_on"}) for task in suggested],
            }, ensure_ascii=False))
        phase = "model"
        result = await model_factory(temperature=0).with_structured_output(QueryPlan).ainvoke(messages)
        phase = "validation"
        plan = QueryPlan.model_validate(result).model_copy(deep=True)
        _validate_plan(plan)
        plan.decision_source = "model"
        plan = ground_task_conditions(plan, question, catalog, state.user_memories)
        record_event("planner", mode="model")
        plan.selected_recipe_name = None
        plan.recipe_selection_error = None
        return plan
    except Exception as exc:
        fallback_routes = routes or ["additional_query"]
        record_event("planner", mode="fallback", phase=phase, reason=type(exc).__name__)
        logger.exception("Task planner failed; using deterministic fallback")
        plan = QueryPlan(tasks=_fallback_tasks(question, fallback_routes), constraints=seed,
                         decision_source="fallback")
        return ground_task_conditions(plan, question, catalog, state.user_memories)
