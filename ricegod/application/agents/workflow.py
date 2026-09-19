"""Wire the graph, dispatch dependency rounds, and isolate task failures."""

import asyncio
import logging
import inspect
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Overwrite, Send

from ricegod.application.agents import answer, planner, recipe_retrieval, knowledge_retrieval
from ricegod.application.agents.state import AgentState, InputState, TaskResult
from ricegod.config import settings
from ricegod.infrastructure.checkpoint_store import checkpoint_serializer
from ricegod.application.performance import measure_stage, record_event

logger = logging.getLogger(__name__)


def build_graph(analyze_intent, general_query, additional_query, model_factory, graph_repository, local_repository,
                *, checkpointer: BaseCheckpointSaver | None = None):
    async def plan_tasks(state: AgentState):
        plan = await planner.make_plan(state, graph_repository, model_factory)
        return {"plan": plan, "task_results": Overwrite({}), "sources": [], "answer": ""}

    def dispatch(state: AgentState):
        pending = [task for task in state.plan.tasks if task.id not in state.task_results]
        if not pending:
            return "synthesize_answer"
        ready = [task for task in pending if all(key in state.task_results for key in task.depends_on)]
        if not ready:
            raise ValueError("Task dependency graph has no runnable task")
        return [Send("execute_task", {"task": task, "plan": state.plan, "state": state,
                                       "upstream": [state.task_results[key] for key in task.depends_on]})
                for task in ready]

    async def execute_task(payload: dict[str, Any]):
        task = payload["task"]
        try:
            async def run():
                if task.route == "recipe_query":
                    result = await recipe_retrieval.retrieve_recipe(task, payload["plan"], graph_repository, local_repository, model_factory)
                elif task.route == "food_knowledge":
                    result = await knowledge_retrieval.retrieve_knowledge(task, payload["upstream"])
                else:
                    handler = general_query if task.route == "general_query" else additional_query
                    response = await handler(payload["state"])
                    result = TaskResult(task_id=task.id, route=task.route, question=task.question,
                                        content=str(response["messages"][-1].content))
                return result
            stage_name = {"recipe_query": "recipe_retrieval", "food_knowledge": "knowledge_retrieval"}.get(task.route, "conversation")
            with measure_stage(stage_name, task_id=task.id):
                result = await asyncio.wait_for(run(), timeout=settings.AGENT_TASK_TIMEOUT_SECONDS)
        except Exception as exc:
            logger.exception("Task failed: %s", task.id)
            result = TaskResult(task_id=task.id, route=task.route, question=task.question, status="failed",
                                error_code="task_timeout" if isinstance(exc, (TimeoutError, asyncio.TimeoutError))
                                else "retrieval_unavailable")
        record_event("task", task_id=task.id, route=task.route, status=result.status, error_code=result.error_code)
        return {"task_results": {task.id: result}}

    def finish_round(state: AgentState):
        return {}

    async def synthesize_answer(state: AgentState):
        with measure_stage("answer"):
            return await answer.synthesize_answer(state, model_factory)

    async def timed_intent(state: AgentState):
        with measure_stage("router"):
            if inspect.iscoroutinefunction(analyze_intent):
                return await analyze_intent(state)
            result = await asyncio.to_thread(analyze_intent, state)
            return await result if inspect.isawaitable(result) else result

    async def timed_plan(state: AgentState):
        with measure_stage("planner"):
            return await plan_tasks(state)

    builder = StateGraph(AgentState, input_schema=InputState)
    builder.add_node("analyze_intent", timed_intent)
    builder.add_node("plan_tasks", timed_plan)
    builder.add_node("execute_task", execute_task)
    builder.add_node("finish_round", finish_round)
    builder.add_node("synthesize_answer", synthesize_answer)
    builder.add_edge(START, "analyze_intent")
    builder.add_edge("analyze_intent", "plan_tasks")
    builder.add_conditional_edges("plan_tasks", dispatch, ["execute_task", "synthesize_answer"])
    builder.add_edge("execute_task", "finish_round")
    builder.add_conditional_edges("finish_round", dispatch, ["execute_task", "synthesize_answer"])
    builder.add_edge("synthesize_answer", END)
    if checkpointer is None:
        # Explicit graph construction in isolated unit tests remains independent of PostgreSQL.
        checkpointer = InMemorySaver(serde=checkpoint_serializer())
    return builder.compile(checkpointer=checkpointer)
