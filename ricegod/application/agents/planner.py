"""Build and ground task plans against the current request and conversation."""

import json
import logging
from typing import Callable


from ricegod.application.agents.state import AgentState, PlannedTask, QueryPlan
from ricegod.application.agents.task_conditions import ground_task_conditions, meal_scopes
from ricegod.application.agents.planning_policy import deterministic_plan_reason, DEPENDENT_RECIPE_WORDS
from ricegod.application.agents.recommendations import is_recipe_selection, select_recommendation
from ricegod.application.agents.prompts import PLANNER_PROMPT
from ricegod.application.agents.context import build_model_context
from ricegod.application.performance import record_event
from ricegod.config import settings
from ricegod.infrastructure.recipe_graph_repository import RecipeGraphRepository

logger = logging.getLogger(__name__)


def _fallback_tasks(state: AgentState) -> list[PlannedTask]:
    question = state.question or (str(state.messages[-1].content) if state.messages else "")
    tasks = [PlannedTask(id=f"task_{i}", route=route, question=question)
             for i, route in enumerate(state.router.type or ["general_query"])]
    scopes = meal_scopes(question)
    if scopes and "recipe_query" in state.router.type and len(scopes) + len(tasks) - 1 <= 4:
        tasks = [task for task in tasks if task.route != "recipe_query"] + [
            PlannedTask(id=f"meal_{i}", route="recipe_query", question=scope, source_text=scope)
            for i, scope in enumerate(scopes)]
    recipes = [task for task in tasks if task.route == "recipe_query"]
    if recipes and any(word in question for word in DEPENDENT_RECIPE_WORDS):
        for task in tasks:
            if task.route == "food_knowledge":
                task.depends_on = [recipe.id for recipe in recipes]
        # A dependency always precedes its consumer, even if routing order differs.
        tasks.sort(key=lambda task: bool(task.depends_on))
    return tasks


def _validate_plan(plan: QueryPlan, allowed: list[str]) -> None:
    seen: set[str] = set()
    for task in plan.tasks:
        if not task.id or task.id in seen or not task.question.strip() or task.route not in allowed:
            raise ValueError("Invalid task identity, question or route")
        if any(dependency not in seen for dependency in task.depends_on):
            raise ValueError("Dependencies must reference earlier tasks")
        seen.add(task.id)
    if not plan.tasks or set(allowed) - {task.route for task in plan.tasks}:
        raise ValueError("Plan must cover every routed intent")


async def make_plan(state: AgentState, catalog: RecipeGraphRepository, model_factory: Callable) -> QueryPlan:
    question = state.question or (str(state.messages[-1].content) if state.messages else "")
    seed = catalog.extract_constraints(question, state.user_memories)
    if is_recipe_selection(question):
        record_event("planner", mode="selection")
        selection = select_recommendation(question, state.last_recommendations)
        plan = ground_task_conditions(QueryPlan(tasks=_fallback_tasks(state)), question,
                                      catalog, state.user_memories, selection.name)
        recipes = [task for task in plan.tasks if task.route == "recipe_query"]
        for task in recipes:
            task.recipe_request = task.recipe_request.model_copy(update={
                "recommendation": False, "limit": 1, "requested_count": None, "detailed": True})
        for task in plan.tasks:
            if task.route == "food_knowledge":
                task.depends_on = [recipe.id for recipe in recipes]
        plan.selected_recipe_name = selection.name
        plan.recipe_selection_error = selection.clarification or None
        if recipes:
            plan.recipe_request = recipes[0].recipe_request
        return plan
    plan = QueryPlan(tasks=_fallback_tasks(state), constraints=seed)
    reason = deterministic_plan_reason(question, state.router.type, meal_scopes(question), catalog)
    if reason or not settings.LLM_API_KEY:
        record_event("planner", mode="rules", reason=reason or "llm_unconfigured")
        return ground_task_conditions(plan, question, catalog, state.user_memories)
    if any(route in state.router.type for route in ("recipe_query", "food_knowledge")):
        phase = "context"
        try:
            messages = build_model_context(state, "planner", system_prompt=PLANNER_PROMPT,
                evidence=json.dumps({
                    "allowed_routes": state.router.type,
                    "question": question,
                    "user_memories": state.user_memories,
                    "known_constraints": seed.model_dump(),
                    "suggested_tasks": [task.model_dump(include={"id", "route", "question", "source_text", "depends_on"})
                                        for task in plan.tasks],
                }, ensure_ascii=False))
            phase = "model"
            result = await model_factory(temperature=0).with_structured_output(QueryPlan).ainvoke(messages)
            phase = "validation"
            plan = QueryPlan.model_validate(result).model_copy(deep=True)
            _validate_plan(plan, state.router.type)
            plan = ground_task_conditions(plan, question, catalog, state.user_memories)
            record_event("planner", mode="model")
            plan.selected_recipe_name = None
            plan.recipe_selection_error = None
            return plan
        except Exception as exc:
            record_event("planner", mode="fallback", phase=phase, reason=type(exc).__name__,
                         recipe_task_count=sum(task.route == "recipe_query" for task in plan.tasks))
            logger.exception("Planner failed; using deterministic plan")
            plan = QueryPlan(tasks=_fallback_tasks(state), constraints=seed)
    else:
        record_event("planner", mode="rules")
    # Selection bookkeeping comes exclusively from the conversation snapshot.
    plan.selected_recipe_name = None
    plan.recipe_selection_error = None
    return ground_task_conditions(plan, question, catalog, state.user_memories)
