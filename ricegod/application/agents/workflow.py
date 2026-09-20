"""Wire the graph, dispatch dependency rounds, and isolate task failures."""

import asyncio
import logging
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


def build_graph(general_query, additional_query, model_factory, graph_repository, local_repository,
                *, checkpointer: BaseCheckpointSaver | None = None):
    task_slots: asyncio.Semaphore | None = None
    task_slots_loop = None
    detached_tasks: set[asyncio.Task] = set()

    def execution_slots() -> asyncio.Semaphore:
        nonlocal task_slots, task_slots_loop
        loop = asyncio.get_running_loop()
        if task_slots is None or task_slots_loop is not loop:
            task_slots = asyncio.Semaphore(settings.AGENT_MAX_CONCURRENT_TASKS)
            task_slots_loop = loop
        return task_slots

    def observe_detached_task(task: asyncio.Task) -> None:
        detached_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error("Detached timed-out task ended with an error.",
                         exc_info=(type(error), error, error.__traceback__))

    async def plan_tasks(state: AgentState):
        plan = await planner.make_plan(state, graph_repository, model_factory)
        question = state.question or (str(state.messages[-1].content) if state.messages else "")
        return {"plan": plan, "router": planner.router_summary(plan, question),
                "task_results": Overwrite({}), "sources": [], "answer": ""}

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
                async def bounded_run():
                    # Sync drivers cannot be force-cancelled once dispatched to a
                    # worker thread. Keep timed-out work alive and cap its total
                    # concurrency instead of pretending wait_for stopped it.
                    async with execution_slots():
                        return await run()

                worker = asyncio.create_task(bounded_run())
                try:
                    result = await asyncio.wait_for(
                        asyncio.shield(worker), timeout=settings.AGENT_TASK_TIMEOUT_SECONDS)
                except (TimeoutError, asyncio.TimeoutError):
                    detached_tasks.add(worker)
                    worker.add_done_callback(observe_detached_task)
                    raise
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

    async def timed_plan(state: AgentState):
        with measure_stage("planner"):
            return await plan_tasks(state)

    builder = StateGraph(AgentState, input_schema=InputState)
    builder.add_node("plan_tasks", timed_plan)
    builder.add_node("execute_task", execute_task)
    builder.add_node("finish_round", finish_round)
    builder.add_node("synthesize_answer", synthesize_answer)
    builder.add_edge(START, "plan_tasks")
    builder.add_conditional_edges("plan_tasks", dispatch, ["execute_task", "synthesize_answer"])
    builder.add_edge("execute_task", "finish_round")
    builder.add_conditional_edges("finish_round", dispatch, ["execute_task", "synthesize_answer"])
    builder.add_edge("synthesize_answer", END)
    if checkpointer is None:
        # Explicit graph construction in isolated unit tests remains independent of PostgreSQL.
        checkpointer = InMemorySaver(serde=checkpoint_serializer())
    return builder.compile(checkpointer=checkpointer)
