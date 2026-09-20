from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Optional
from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages
from pydantic import BaseModel, Field as PydanticField, field_validator
from ricegod.domain.recipes import RecipeConstraints
from ricegod.domain.recipe_ranking import RecipeRequest

RouteType = Literal[
    "general_query",
    "additional_query",
    "recipe_query",
    "food_knowledge",
]
class Router(BaseModel):
    logic: str = ""
    type: list[RouteType] = PydanticField(default_factory=lambda: ["general_query"], min_length=1)
    question: str = ""

    decision: Optional[str] = None
    confidence: Optional[float] = None
    reasoning: Optional[str] = None

    @field_validator("type", mode="before")
    @classmethod
    def normalize_routes(cls, value):
        routes = [value] if isinstance(value, str) else list(dict.fromkeys(value))
        return routes or ["general_query"]


class RecipeOperationSpec(BaseModel):
    """Planner hint for one recipe task; execution validates every entity."""

    operation: Literal["search", "similar", "substitute"] = "search"
    dish_name: str | None = None
    ingredient: str | None = None
    replacement: str | None = None


class PlannedTask(BaseModel):
    id: str
    route: RouteType
    question: str
    depends_on: list[str] = PydanticField(default_factory=list)
    # Exact span of the current user turn, used to ground this task's filters/count.
    source_text: str = ""
    operation_spec: RecipeOperationSpec | None = None
    operation_error: str | None = None
    constraints: RecipeConstraints | None = None
    recipe_request: RecipeRequest | None = None


class QueryPlan(BaseModel):
    tasks: list[PlannedTask] = PydanticField(default_factory=list, max_length=4)
    decision_source: Literal["rules", "model", "fallback", "selection"] = "rules"
    constraints: RecipeConstraints = PydanticField(default_factory=RecipeConstraints)
    recipe_request: RecipeRequest = PydanticField(default_factory=RecipeRequest)
    selected_recipe_name: str | None = None
    recipe_selection_error: str | None = None
    clarification: str | None = None


class TaskResult(BaseModel):
    task_id: str
    route: RouteType
    question: str
    status: Literal["ok", "empty", "degraded", "failed", "blocked"] = "ok"
    content: str = ""
    records: list[dict[str, Any]] = PydanticField(default_factory=list)
    sources: list[str] = PydanticField(default_factory=list)
    error_code: str | None = None


class RecommendationGroup(BaseModel):
    """One displayed group, preserving its own one-based recipe order."""

    task_id: str
    label: str = "推荐"
    question: str = ""
    records: list[dict[str, Any]] = PydanticField(default_factory=list)


def merge_task_results(left: dict[str, TaskResult], right: dict[str, TaskResult]) -> dict[str, TaskResult]:
    return {**left, **right}


@dataclass(kw_only=True)
class InputState:
    messages: Annotated[list[AnyMessage], add_messages]
    memory_context: str = ""
    user_memories: list[dict[str, Any]] = field(default_factory=list)
    # Business question after an explicitly processed memory operation; the
    # original message remains in messages and in the raw message history.
    question: str = ""
    # Applied memory operations invalidate preceding model inputs, not storage.
    context_history_after: str = ""

@dataclass(kw_only=True)
class AgentState(InputState):
    """State of the retrieval graph / agent."""
    router: Router = field(default_factory=lambda: Router(type=["general_query"], logic=""))
    steps: list[str] = field(default_factory=list)
    answer: str = field(default_factory=str)
    plan: QueryPlan = field(default_factory=QueryPlan)
    task_results: Annotated[dict[str, TaskResult], merge_task_results] = field(default_factory=dict)
    last_recommendations: list[RecommendationGroup] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
