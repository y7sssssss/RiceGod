from pydantic import AliasChoices, BaseModel, Field

from ricegod.application.memory.models import MemoryWriteResult
from ricegod.application.performance import PerformanceReport


class ChatRequest(BaseModel):
    user_id: str = Field(default="anonymous", min_length=1, max_length=128, description="Current user id.")
    conversation_id: str = Field(
        default="default",
        min_length=1,
        max_length=128,
        description="Conversation id for raw message logs and context.",
        validation_alias=AliasChoices("conversation_id", "thread_id"),
    )
    message: str = Field(..., min_length=1, description="User question.")


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
