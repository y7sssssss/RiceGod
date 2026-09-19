"""Classify intents without constructing graph or database resources."""

import logging

from langchain_openai import ChatOpenAI

from ricegod.application.agents.prompts import ROUTER_SYSTEM_PROMPT
from ricegod.application.agents.state import AgentState, RouteType, Router
from ricegod.application.agents.recommendations import is_recipe_selection
from ricegod.application.agents.context import build_model_context
from ricegod.application.performance import record_event
from ricegod.config import settings
from ricegod.infrastructure.llm_client import build_chat_model

logger = logging.getLogger(__name__)


def _latest_question(state: AgentState) -> str:
    if state.question:
        return state.question
    if not state.messages:
        return ""
    return str(state.messages[-1].content)


def _build_llm(*, temperature: float = 0.3) -> ChatOpenAI:
    return build_chat_model(temperature=temperature)


def _heuristic_router(question: str) -> Router:
    """Fallback router for local demos and tests when the LLM is unavailable."""
    text = question.strip()
    if not text:
        return Router(type=["additional_query"], logic="empty question", question=question, confidence=0.6)

    general_keywords = ("你好", "谢谢", "感谢", "你是谁", "hello", "hi")
    recipe_keywords = (
        "怎么做",
        "做法",
        "步骤",
        "食材",
        "配料",
        "材料",
        "用什么",
        "需要什么",
        "推荐",
        "家常菜",
        "能做什么",
        "炒",
        "煮",
        "炖",
        "蒸",
    )
    culture_keywords = (
        "历史",
        "来历",
        "由来",
        "典故",
        "文化",
        "菜系",
        "特点",
        "发展",
        "起源",
        "故事",
    )
    vague_keywords = ("想做菜", "帮我看看", "推荐一个", "吃什么")

    lowered = text.lower()
    is_general = any(keyword in lowered for keyword in general_keywords)
    is_recipe = is_recipe_selection(question) or any(keyword in text for keyword in recipe_keywords)
    is_culture = any(keyword in text for keyword in culture_keywords)
    is_vague = any(keyword in text for keyword in vague_keywords)

    tasks: list[RouteType] = []
    if is_recipe:
        tasks.append("recipe_query")
    if is_culture:
        tasks.append("food_knowledge")
    if not tasks and is_general:
        tasks.append("general_query")
    if not tasks and is_vague:
        tasks.append("additional_query")
    if not tasks:
        tasks.append("food_knowledge")

    route = tasks[0]
    if len(tasks) > 1:
        logic = "keyword fallback: multi-intent query"
    elif route == "general_query":
        logic = "keyword fallback: general chat"
    elif route == "additional_query":
        logic = "keyword fallback: missing recipe details"
    elif route == "recipe_query":
        logic = "keyword fallback: recipe graph query"
    else:
        logic = "keyword fallback: food culture knowledge"

    return Router(type=tasks, logic=logic, question=question, confidence=0.55)


async def analyze_intent(state: AgentState) -> dict[str, Router]:
    """LangGraph node: classify the latest user message and update state."""
    question = _latest_question(state)
    if is_recipe_selection(question):
        record_event("router", mode="selection")
        # Ordinals are resolved deterministically from this conversation's
        # snapshot, even when the question contains no explicit recipe verb.
        return {"router": _heuristic_router(question)}
    if not settings.LLM_API_KEY or not settings.USE_LLM_ROUTER:
        record_event("router", mode="rules")
        return {"router": _heuristic_router(question)}

    model = _build_llm(temperature=0)
    try:
        messages = build_model_context(state, "router", system_prompt=ROUTER_SYSTEM_PROMPT)
        result = await model.with_structured_output(Router).ainvoke(messages)
        result = Router.model_validate(result)
    except Exception as exc:
        record_event("router", mode="fallback", reason=type(exc).__name__)
        logger.exception("LLM router failed; falling back to heuristic router.")
        return {"router": _heuristic_router(question)}

    if not result.question:
        result.question = question
    record_event("router", mode="model")
    return {"router": result}


if __name__ == "__main__":
    from ricegod.cli import main
    main()
