"""Deterministic grounding of memory facts in real user messages."""

from typing import Any

from ricegod.application.memory.models import MemoryItem


def normalize_text(text: str) -> str:
    return " ".join(text.split())


def latest_user_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Only the newest user turn may create or replace facts during this request."""
    for message in reversed(messages):
        if (message.get("role") == "user" and type(message.get("id")) is int
                and message["id"] > 0 and str(message.get("content", "")).strip()):
            return [message]
    return []


def find_user_evidence(messages: list[dict[str, Any]], source_message_id: int, evidence: str) -> str | None:
    quote = normalize_text(evidence)
    if type(source_message_id) is not int or source_message_id <= 0 or not 2 <= len(quote) <= 300:
        return None
    for message in messages:
        if message.get("id") != source_message_id or message.get("role") != "user":
            continue
        if quote in normalize_text(str(message.get("content", ""))):
            return quote
    return None


def ground_memory(item: MemoryItem, messages: list[dict[str, Any]]) -> MemoryItem | None:
    quote = find_user_evidence(messages, item.source_message_id, item.evidence)
    if quote is None:
        return None
    # Saving the verified quote prevents a valid source id from laundering an
    # unsupported paraphrase, including preferences copied from an assistant.
    return item.model_copy(update={"content": quote, "evidence": quote})
