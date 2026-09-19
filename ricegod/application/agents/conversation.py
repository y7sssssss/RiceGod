"""Handle general dialogue and requests that need clarification."""

from langchain_core.messages import AIMessage, BaseMessage

from ricegod.application.agents.prompts import GENERAL_QUERY_SYSTEM_PROMPT, ADDITIONAL_QUERY_SYSTEM_PROMPT
from ricegod.application.agents.state import AgentState
from ricegod.application.memory.confirmation import MEMORY_PENDING_PROMPT
from ricegod.config import settings
from ricegod.infrastructure.llm_client import build_chat_model
from ricegod.application.agents.context import build_model_context, ContextBudgetExceeded


def _build_llm(*, temperature: float = 0.3):
    return build_chat_model(temperature=temperature)


def _messages_with_memory_context(state: AgentState) -> list[BaseMessage]:
    return build_model_context(state, "conversation")


async def _reply(state, system_prompt, model):
    try:
        messages = build_model_context(state, "conversation", system_prompt=system_prompt + "\n" + MEMORY_PENDING_PROMPT)
    except ContextBudgetExceeded:
        return {"messages": [AIMessage(content="本次问题或必要上下文过长，请缩短问题或分开提问。") ]}
    return {"messages": [await model.ainvoke(messages)]}


async def general_query(state: AgentState) -> dict[str, list[BaseMessage]]:
    """Generate a friendly general response."""
    if not settings.LLM_API_KEY:
        return {
            "messages": [
                AIMessage(content="你好，我是 RiceGod，可以帮你查询菜谱做法、食材搭配和饮食文化知识。")
            ]
        }
    model = _build_llm(temperature=0.7)
    return await _reply(state, GENERAL_QUERY_SYSTEM_PROMPT, model)


async def additional_query(state: AgentState) -> dict[str, list[BaseMessage]]:
    """Ask focused follow-up questions when the user's recipe request is vague."""
    if not settings.LLM_API_KEY:
        return {
            "messages": [
                AIMessage(content="可以的。你想做哪道菜，或者手头有哪些食材？也可以告诉我偏好的口味。")
            ]
        }
    model = _build_llm(temperature=0.7)
    return await _reply(state, ADDITIONAL_QUERY_SYSTEM_PROMPT, model)
