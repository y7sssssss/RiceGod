from uuid import uuid4

from pydantic import AliasChoices, BaseModel, Field

from ricegod.application.memory.models import MemoryWriteResult
from ricegod.application.performance import PerformanceReport


class ChatRequest(BaseModel):
    request_id: str = Field(
        default_factory=lambda: str(uuid4()),
        min_length=1,
        max_length=128,
        description="Caller-stable idempotency key for one chat turn.",
    )
    user_id: str = Field(default="anonymous", min_length=1, max_length=128, description="Current user id.")
    conversation_id: str = Field(
        default="default",
        min_length=1,
        max_length=128,
        description="Conversation id for raw message logs and context.",
        validation_alias=AliasChoices("conversation_id", "thread_id"),
    )
    message: str = Field(..., min_length=1, max_length=20_000, description="User question.")


class UsedMemory(BaseModel):
    id: int
    type: str
    content: str
    score: float


class ChatResponse(BaseModel):
    answer: str
    route: str
    logic: str
    sources: list[str] = Field(default_factory=list)
    used_memories: list[UsedMemory] = Field(default_factory=list)
    memory_write: MemoryWriteResult = Field(default_factory=lambda: MemoryWriteResult(available=False))
    performance: PerformanceReport | None = None


class DeleteMemoryResponse(BaseModel):
    deleted: bool
