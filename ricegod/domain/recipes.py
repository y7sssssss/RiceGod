import re
from typing import Any

from pydantic import BaseModel, Field

from ricegod.domain.graph_v2 import allergens_for_ingredient, canonical_ingredient, mentioned_allergens


SPICY_INGREDIENT_MARKERS = ("辣椒", "辣油", "辣酱", "泡椒", "剁椒", "小米椒")
NO_SPICY = ("不吃辣", "不能吃辣", "不要辣", "不放辣", "忌辣", "不喜欢辣")


class RecipeConstraints(BaseModel):
    """Hard retrieval filters; preferences remain in the answer context."""

    dish_name: str | None = None
    required_ingredients: list[str] = Field(default_factory=list)
    excluded_ingredients: list[str] = Field(default_factory=list)
    flavors: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    dish_types: list[str] = Field(default_factory=list)
    excluded_allergens: list[str] = Field(default_factory=list)
    avoid_spicy: bool = False

    def matches(self, recipe: dict[str, Any]) -> bool:
        name = recipe.get("name") or recipe.get("菜名", "")
        if self.dish_name and name != self.dish_name:
            return False
        ingredients: list[str] = []
        for key in ("主食材", "辅料"):
            for item in recipe.get(key) or []:
                if isinstance(item, dict):
                    ingredients.append(canonical_ingredient(str(item.get("name") or "")))
                elif isinstance(item, (list, tuple)) and item:
                    ingredients.append(canonical_ingredient(str(item[0])))
                elif isinstance(item, str):
                    ingredients.append(canonical_ingredient(item))
        required = [canonical_ingredient(item) for item in self.required_ingredients]
        excluded = [canonical_ingredient(item) for item in self.excluded_ingredients]
        if any(not any(wanted in item for item in ingredients) for wanted in required):
            return False
        if any(banned in item for banned in excluded for item in ingredients):
            return False
        actual_allergens = {allergen for ingredient in ingredients for allergen in allergens_for_ingredient(ingredient)}
        if actual_allergens.intersection(self.excluded_allergens):
            return False
        for values, key in ((self.flavors, "口味"), (self.methods, "工艺"), (self.dish_types, "类型")):
            actual = recipe.get(key) or []
            actual = [actual] if isinstance(actual, str) else actual
            if any(value not in actual for value in values):
                return False
        flavors = recipe.get("口味") or []
        if self.avoid_spicy and (
            "辣" in str(flavors)
            or any(marker in ingredient for marker in SPICY_INGREDIENT_MARKERS for ingredient in ingredients)
        ):
            return False
        return True


def _mentions(text: str, values: list[str], min_length: int = 2) -> list[str]:
    """Prefer the longest entity at each position, avoiding nested duplicate names."""
    found: list[str] = []
    remaining = text
    for value in sorted(values, key=len, reverse=True):
        if len(value) >= min_length and value in remaining:
            found.append(value)
            remaining = remaining.replace(value, " " * len(value))
    return found


def _negative(text: str, value: str) -> bool:
    for clause in re.split(r"[，,。；;\n]|但是|不过|但", text):
        if value not in clause:
            continue
        before, _, after = clause.partition(value)
        if len(value) == 1:
            # A one-character ingredient must follow a restriction or list separator,
            # rather than merely occur inside a longer name such as 香菜 / 家常菜.
            negative_prefix = re.search(
                r"(?:不吃|不能吃|不喜欢|不要|不放|不加|避免|忌口|排除)(?:\s*|.{0,15}[、和与及]\s*)$", before
            )
            allergy_prefix = before.rstrip().endswith(("对", "、", "和", "与", "及")) and "过敏" in after
            if negative_prefix or allergy_prefix:
                return True
            continue
        if re.search(r"不吃|不能吃|不喜欢|不要|不放|不加|避免|忌口|排除", before) or "过敏" in after:
            return True
    return False


def extract_constraints(
    question: str,
    *,
    ingredients: list[str],
    flavors: list[str],
    methods: list[str],
    dish_types: list[str],
    dish_names: list[str],
    memories: list[dict[str, Any]],
) -> RecipeConstraints:
    """Deterministic fallback for known entities, never infer an allergy from preferences."""
    dishes = _mentions(question, dish_names)
    entity_question = question
    for dish in dishes:
        entity_question = entity_question.replace(dish, " ")
    mentioned = _mentions(entity_question, ingredients)
    excluded = [value for value in _mentions(entity_question, ingredients, min_length=1)
                if _negative(entity_question, value)]
    constraint_texts = [question]
    for memory in memories:
        type_ = memory.get("type")
        content = str(memory.get("content", ""))
        if type_ in ("allergy", "disliked_ingredient"):
            # These typed memories are already explicit restrictions, not general dialogue.
            entities = _mentions(content, ingredients, min_length=1)
            negatives = [value for value in entities if _negative(content, value)]
            excluded.extend(negatives or entities)
        if type_ in ("allergy", "disliked_ingredient", "dietary_preference"):
            constraint_texts.append(content)
    return RecipeConstraints(
        dish_name=dishes[0] if len(dishes) == 1 else None,
        required_ingredients=list(dict.fromkeys(canonical_ingredient(value) for value in mentioned if value not in excluded)),
        excluded_ingredients=list(dict.fromkeys(canonical_ingredient(value) for value in excluded)),
        excluded_allergens=list(dict.fromkeys(
            allergen for text in constraint_texts for allergen in mentioned_allergens(text))),
        flavors=[value for value in _mentions(entity_question, flavors, min_length=1) if not _negative(entity_question, value)],
        methods=[value for value in _mentions(entity_question, methods, min_length=1) if not _negative(entity_question, value)],
        dish_types=_mentions(entity_question, dish_types),
        avoid_spicy=any(marker in text for text in constraint_texts for marker in NO_SPICY),
    )
