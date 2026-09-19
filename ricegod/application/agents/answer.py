"""Synthesize evidence and preserve the displayed recommendation snapshot."""

import json
import logging
from typing import Callable

from langchain_core.messages import AIMessage

from ricegod.application.agents.state import AgentState
from ricegod.application.agents.prompts import ANSWER_PROMPT
from ricegod.application.agents.recommendations import new_recommendations, recommendations_in_order
from ricegod.application.agents.recipe_formatting import format_recipe_evidence
from ricegod.application.agents.context import build_model_context
from ricegod.application.performance import record_event
from ricegod.application.memory.confirmation import strip_memory_claims
from ricegod.domain.recipe_ranking import build_recipe_request, display_name
from ricegod.config import settings

logger = logging.getLogger(__name__)


def _evidence_results(state: AgentState) -> list[dict]:
    """Avoid duplicating records as both JSON and formatted text."""
    results = []
    for task in state.plan.tasks:
        result = state.task_results[task.id].model_dump()
        request = task.recipe_request or state.plan.recipe_request
        if result["records"]:
            result["content"] = ""  # Original content remains available for fallback.
            if task.route == "recipe_query" and request.recommendation and not request.detailed:
                result["records"] = [{key: value for key, value in row.items()
                                      if key not in ("做法", "steps", "procedure")}
                                     for row in result["records"]]
        results.append(result)
    return results


async def synthesize_answer(state: AgentState, model_factory: Callable):
    mode = "deterministic"
    results = [state.task_results[task.id] for task in state.plan.tasks]
    sources = list(dict.fromkeys(source for result in results for source in result.sources))
    groups = new_recommendations(state.plan, state.task_results)
    labels = {group.task_id: group.label for group in groups}
    if state.plan.recipe_selection_error:
        answer = state.plan.recipe_selection_error
    elif len(results) == 1 and results[0].route in ("general_query", "additional_query"):
        answer = results[0].content or "当前暂时无法回答，请稍后重试。"
    else:
        titles = {"recipe_query": "菜谱查询", "food_knowledge": "饮食文化", "general_query": "对话", "additional_query": "补充信息"}
        recommendation_content = {}
        for group in groups:
            task = next(task for task in state.plan.tasks if task.id == group.task_id)
            request = task.recipe_request or (state.plan.recipe_request if state.plan.recipe_request.query
                                              else build_recipe_request(task.question, task.constraints or state.plan.constraints))
            recommendation_content[group.task_id] = format_recipe_evidence(group.records, request)
        fallback = "\n\n".join(
            f"{labels.get(result.task_id, titles[result.route])}：\n" + (recommendation_content.get(result.task_id, result.content) if result.status in ("ok", "degraded", "empty")
                                          else "该部分暂时没有可用的检索依据。")
            for result in results)
        if settings.LLM_API_KEY and any(result.status in ("ok", "degraded") for result in results):
            try:
                messages = build_model_context(state, "answer", system_prompt=ANSWER_PROMPT,
                    evidence=json.dumps({"tasks": [task.model_dump() for task in state.plan.tasks],
                                                      "recommendation_groups": [{"task_id": group.task_id,
                                                          "label": group.label, "recipe_names": [display_name(row) for row in group.records]}
                                                          for group in groups],
                                                      "task_results": _evidence_results(state)},
                                                     ensure_ascii=False, default=str))
                response = await model_factory(temperature=0.3).ainvoke(messages)
                answer = str(response.content)
                mode = "model"
            except Exception as exc:
                record_event("answer_fallback", reason=type(exc).__name__)
                mode = "fallback"
                logger.exception("Answer synthesis failed; returning deterministic evidence")
                answer = fallback
        else:
            answer = fallback
    answer = strip_memory_claims(answer)[0] or "收到你的要求。"
    if groups and not recommendations_in_order(answer, groups):
        # The snapshot must match displayed recipe order, including when
        # synthesis omits/reorders evidence or an unsafe claim is removed.
        logger.warning("Recommendation answer order differs from evidence; using formatted evidence.")
        answer = strip_memory_claims(fallback)[0] or "收到你的要求。"
        mode = "fallback"
        record_event("answer_fallback", reason="recommendation_order")
    record_event("answer", mode=mode)
    return {"messages": [AIMessage(content=answer)], "answer": answer, "sources": sources,
            "last_recommendations": groups or state.last_recommendations}
