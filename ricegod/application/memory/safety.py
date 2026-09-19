from __future__ import annotations

import re

from ricegod.application.memory.models import MemoryItem


_SENSITIVE_PATTERNS = (
    re.compile(r"\b(?:api[_-]?key|token|password|passwd|secret)\b", re.IGNORECASE),
    re.compile(r"\b\d{4,8}\b"),  # simple OTP/code-like pattern
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),  # common API key prefix
)


def is_sensitive_text(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return True
    return any(pattern.search(normalized) for pattern in _SENSITIVE_PATTERNS)


def filter_sensitive_memories(memories: list[MemoryItem]) -> list[MemoryItem]:
    return [item for item in memories if not is_sensitive_text(item.content)]
