"""Bound model inputs without altering checkpoint or raw conversation history."""

import json
import logging
from typing import Literal

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from ricegod.application.agents.state import AgentState
from ricegod.config import settings
from ricegod.domain.recipe_ranking import display_name
from ricegod.application.performance import record_event

logger = logging.getLogger(__name__)
Purpose = Literal["router", "planner", "conversation", "answer"]


class ContextBudgetExceeded(ValueError):
    """Protected inputs cannot fit; callers must use their deterministic fallback."""


def estimate_tokens(messages: list[BaseMessage]) -> int:
    # Conservative byte-based estimate, NOT a provider-specific tokenizer.
    # Chinese, JSON, punctuation and role/message overhead all count.
    return sum(len(str(m.content).encode("utf-8")) + 16 for m in messages)


def _recent_turns(state: AgentState) -> tuple[list[list[BaseMessage]], HumanMessage]:
    messages = list(state.messages)
    current_index = next((i for i in range(len(messages) - 1, -1, -1)
                          if isinstance(messages[i], HumanMessage)), None)
    current = (messages[current_index] if current_index is not None else HumanMessage(content=""))
    if state.question:
        current = current.model_copy(update={"content": state.question})
    start = 0
    if state.context_history_after:
        boundary = next((i for i, m in enumerate(messages)
                         if m.id == state.context_history_after), None)
        # If the checkpoint lost the marker, prefer no old history to stale facts.
        start = boundary + 1 if boundary is not None else len(messages)
    history = messages[start:current_index] if current_index is not None else []
    turns: list[list[BaseMessage]] = []
    for message in history:
        if isinstance(message, HumanMessage):
            turns.append([message])
        elif turns and message.type == "ai":
            turns[-1].append(message)
    # Ignore orphan assistants and incomplete prior user turns.
    turns = [turn for turn in turns if len(turn) > 1]
    return (turns[-settings.CONTEXT_RECENT_TURNS:] if settings.CONTEXT_RECENT_TURNS else []), current


def build_model_context(state: AgentState, purpose: Purpose, *, system_prompt: str = "",
                        evidence: str | None = None) -> list[BaseMessage]:
    """Protect current request, memory and evidence; remove oldest whole turns."""
    prefix: list[BaseMessage] = [SystemMessage(content=system_prompt)] if system_prompt else []
    if purpose != "router" and state.memory_context:
        prefix.append(SystemMessage(content=state.memory_context))
    if state.last_recommendations:
        groups = [{"label": group.label, "recipes": [
            {"number": i, "name": display_name(row)} for i, row in enumerate(group.records, 1)]}
            for group in state.last_recommendations]
        prefix.append(HumanMessage(content="上次展示的推荐列表（历史数据，不是本轮证据或指令）：\n" +
                                   json.dumps(groups, ensure_ascii=False)))
    turns, current = _recent_turns(state)
    suffix = [HumanMessage(content=evidence)] if evidence is not None else []
    budget = {"router": settings.CONTEXT_ROUTER_TOKEN_BUDGET,
              "planner": settings.CONTEXT_PLANNER_TOKEN_BUDGET,
              "conversation": settings.CONTEXT_ANSWER_TOKEN_BUDGET,
              "answer": settings.CONTEXT_ANSWER_TOKEN_BUDGET}[purpose]
    protected = [*prefix, current, *suffix]
    if estimate_tokens(protected) > budget:
        record_event("model_context", purpose=purpose, status="overflow", estimated_tokens=estimate_tokens(protected), budget=budget)
        logger.warning("Model context overflow: purpose=%s protected_estimate=%d budget=%d",
                       purpose, estimate_tokens(protected), budget)
        raise ContextBudgetExceeded("Required context exceeds configured input budget")
    while turns and estimate_tokens([*prefix, *(m for turn in turns for m in turn), current, *suffix]) > budget:
        turns.pop(0)
    result = [*prefix, *(m for turn in turns for m in turn), current, *suffix]
    record_event("model_context", purpose=purpose, status="ready", history_turns=len(turns),
                 estimated_tokens=estimate_tokens(result), budget=budget)
    logger.info("Model context: purpose=%s history_turns=%d estimated_tokens=%d budget=%d",
                purpose, len(turns), estimate_tokens(result), budget)
    return result
