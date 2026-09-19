"""Strict ground-truth schema shared by rule and live evaluations."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecipeExpectation(Spec):
    scope: str = ""
    count: int = Field(ge=0, le=10)
    allow_fewer: bool = False
    dish_name: str | None = None
    required: list[str] = Field(default_factory=list)
    excluded: list[str] = Field(default_factory=list)
    flavors: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    dish_types: list[str] = Field(default_factory=list)
    avoid_spicy: bool = False


class SelectionExpectation(Spec):
    from_turn: str
    group: int = Field(default=0, ge=0)
    number: int = Field(ge=1)


class Turn(Spec):
    id: str
    message: str = Field(min_length=1)
    conversation: str = "default"
    modes: list[Literal["offline", "live"]] = Field(default_factory=lambda: ["offline", "live"])
    routes: list[str] | None = None
    recipes: list[RecipeExpectation] | None = None
    selection: SelectionExpectation | None = None
    clarification: str | None = None
    conflict_clarification: str | None = None
    memory_status: str | None = None
    extraction_status: str | None = None
    memory_contains: list[str] = Field(default_factory=list)
    memory_absent: list[str] = Field(default_factory=list)
    culture_evidence: bool = False
    recipe_dependency: bool = False
    baseline: bool = False
    history_fixture_turns: int = Field(default=0, ge=0, le=100)


class SeedMemory(Spec):
    type: str
    content: str


class Scenario(Spec):
    id: str
    seeds: list[SeedMemory] = Field(default_factory=list)
    turns: list[Turn] = Field(min_length=1)


class EvaluationSuite(Spec):
    version: str
    description: str
    scenarios: list[Scenario] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_ids_and_references(self):
        ids = set()
        scenario_ids = set()
        for scenario in self.scenarios:
            if scenario.id in scenario_ids:
                raise ValueError("Duplicate scenario id")
            scenario_ids.add(scenario.id)
            prior: dict[str, Turn] = {}
            for turn in scenario.turns:
                if turn.id in ids:
                    raise ValueError("Duplicate turn id")
                ids.add(turn.id)
                if turn.selection:
                    source = prior.get(turn.selection.from_turn)
                    if source is None or source.conversation != turn.conversation or not set(turn.modes) <= set(source.modes):
                        raise ValueError("Selection must reference an earlier compatible turn in the same conversation")
                prior[turn.id] = turn
        return self
