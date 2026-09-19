from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, computed_field


MemoryType = Literal[
    "dietary_preference",
    "allergy",
    "disliked_ingredient",
    "liked_cuisine",
    "cooking_constraint",
    "equipment",
    "household",
    "goal",
    "recipe_feedback",
    "todo",
]

HIGH_PRIORITY_MEMORY_TYPES: tuple[MemoryType, ...] = (
    "allergy",
    "disliked_ingredient",
    "goal",
    "equipment",
)
CONSTRAINT_MEMORY_TYPES: tuple[MemoryType, ...] = ("allergy", "disliked_ingredient")


class MemoryItem(BaseModel):
    type: MemoryType
    content: str = Field(..., min_length=2, max_length=300)
    source_message_id: int = Field(..., gt=0, strict=True)
    evidence: str = Field(..., min_length=2, max_length=300)


class MemoryExtractionResult(BaseModel):
    memories: list[MemoryItem] = Field(default_factory=list)


class MessageRecord(BaseModel):
    id: int
    conversation_id: str
    user_id: str
    role: str
    content: str
    created_at: datetime


class MemoryRecord(BaseModel):
    id: int
    user_id: str
    type: MemoryType
    content: str
    created_at: datetime
    updated_at: datetime
    # Legacy rows remain readable; new writes require validated user evidence.
    source_message_id: int | None = None
    evidence: str = ""


class MemorySearchResult(MemoryRecord):
    score: float


class MemoryOperationReceipt(BaseModel):
    action: Literal["update", "delete"]
    status: Literal["applied", "not_found", "ambiguous", "rejected", "failed"]
    memory_id: int | None = None
    message: str = ""


class MemoryWriteResult(BaseModel):
    """Receipt from completed storage operations, never from an LLM's promise."""

    available: bool = True
    saved: list[MemoryRecord] = Field(default_factory=list)
    unchanged: list[MemoryRecord] = Field(default_factory=list)
    deleted_ids: list[int] = Field(default_factory=list)
    failed_count: int = 0
    skipped_count: int = 0
    extraction_status: Literal["not_run", "skipped", "completed"] = "not_run"
    extraction_skip_reason: str | None = None
    operation: MemoryOperationReceipt | None = None

    @computed_field
    @property
    def status(self) -> str:
        confirmed = bool(self.saved or self.unchanged or self.deleted_ids)
        if not self.available:
            return "unavailable"
        if self.operation and self.operation.status != "applied":
            return "needs_clarification" if self.operation.status == "ambiguous" else self.operation.status
        if self.failed_count or self.skipped_count:
            return "partial" if confirmed else ("failed" if self.failed_count else "skipped")
        if self.saved:
            if self.operation and self.operation.action == "update":
                return "updated"
            return "saved"
        if self.deleted_ids:
            return "deleted"
        return "unchanged" if self.unchanged else "no_candidates"
