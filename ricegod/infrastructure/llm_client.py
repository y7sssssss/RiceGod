"""Shared chat model configuration for the agent and memory pipeline."""

from langchain_openai import ChatOpenAI

from ricegod.config import settings


def build_chat_model(*, temperature: float = 0.3) -> ChatOpenAI:
    if not settings.LLM_API_KEY:
        raise RuntimeError("LLM_API_KEY is not configured.")
    options = {}
    if settings.LLM_ENABLE_THINKING is not None:
        options["extra_body"] = {"enable_thinking": settings.LLM_ENABLE_THINKING}
    return ChatOpenAI(
        model=settings.LLM_MODEL,
        api_key=settings.LLM_API_KEY,
        base_url=settings.LLM_BASE_URL,
        temperature=temperature,
        timeout=settings.LLM_TIMEOUT_SECONDS,
        max_retries=1,
        **options,
    )
