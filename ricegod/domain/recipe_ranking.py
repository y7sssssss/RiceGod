"""Deterministic recipe ranking, shared by graph retrieval and local fallback."""

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from ricegod.domain.recipes import RecipeConstraints, _mentions, _negative


MAX_RECOMMENDATIONS = 10
CANDIDATE_LIMIT = 60
TIME_POINTS = {"十分钟": 30, "廿分钟": 26, "半小时": 22, "三刻钟": 16,
               "一小时": 10, "数小时": 2, "一天": 0, "数天": 0}


class RecipeRequest(BaseModel):
    query: str = ""
    operation: Literal["search", "similar", "substitute"] = "search"
    recommendation: bool = False
    limit: int = Field(default=3, ge=1, le=MAX_RECOMMENDATIONS)
    requested_count: int | None = None
    detailed: bool = False
    prefer_quick: bool = False
    prefer_simple: bool = False
    preferred_flavors: list[str] = Field(default_factory=list)
    preferred_methods: list[str] = Field(default_factory=list)


def _number(text: str) -> int:
    if text.isdigit():
        return int(text)
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
    if "十" in text:
        left, _, right = text.partition("十")
        return digits.get(left, 1) * 10 + digits.get(right, 0)
    return digits.get(text, 0)


def build_recipe_request(query: str, constraints: RecipeConstraints, *,
                         memories: list[dict[str, Any]] | None = None,
                         flavors: list[str] | None = None,
                         methods: list[str] | None = None) -> RecipeRequest:
    # Quantities of eggs / servings and ordinal references are not dish counts.
    matches = list(re.finditer(r"(?<![第\d一二两三四五六七八九十])"
        r"(\d+|[一二两三四五六七八九十]{1,3})\s*"
        r"(?:道|(?:个|种|款)(?=\s*(?:菜|家常菜|食谱|做法)))", query))
    count = _number(matches[-1].group(1)) if matches else None
    if count is not None and count <= 0:
        count = None
    operation: Literal["search", "similar", "substitute"] = "search"
    if constraints.dish_name and any(word in query for word in ("相似", "类似", "差不多")):
        operation = "similar"
    elif constraints.required_ingredients and any(word in query for word in ("替代", "代替", "替换")):
        operation = "substitute"
    recommendation = operation == "similar" or any(word in query for word in
        ("推荐", "有哪些", "有什么", "吃什么", "能做什么", "找几道", "做哪些")) or (
        constraints.dish_name is None and bool(count or constraints.required_ingredients
                                               or constraints.flavors or constraints.methods or constraints.dish_types))
    if operation == "substitute":
        recommendation = False
    preferred_flavors, preferred_methods = [], []
    for memory in memories or []:
        if memory.get("type") not in ("dietary_preference", "liked_cuisine", "cooking_constraint"):
            continue
        content = str(memory.get("content", ""))
        preferred_flavors.extend(value for value in _mentions(content, flavors or [], min_length=1)
                                 if not _negative(content, value))
        preferred_methods.extend(value for value in _mentions(content, methods or [], min_length=1)
                                 if not _negative(content, value))
    return RecipeRequest(query=query, operation=operation, recommendation=recommendation,
        limit=min(count or (3 if recommendation else 1), MAX_RECOMMENDATIONS), requested_count=count,
        detailed=any(word in query for word in ("怎么做", "步骤", "做法", "详细", "材料", "配料")),
        prefer_quick=any(word in query for word in ("快手", "快速", "省时", "赶时间", "十分钟", "10分钟")),
        prefer_simple=any(word in query for word in ("简单", "新手", "容易", "少步骤")),
        preferred_flavors=list(dict.fromkeys(preferred_flavors)),
        preferred_methods=list(dict.fromkeys(preferred_methods)))


def recipe_reference_number(query: str) -> int | None:
    match = re.search(r"第(\d+|[零〇一二两三四五六七八九十]{1,3})(?:道|个|款)", query)
    if not match or re.match(r"问题|步骤|食材|材料", query[match.end():]):
        return None
    return _number(match.group(1))


def resolve_recipe_reference(query: str, recipes: list[dict[str, Any]]) -> str | None:
    if not any(word in query for word in ("怎么做", "步骤", "做法", "详细", "材料", "配料")):
        return None
    number = recipe_reference_number(query)
    if number is None:
        return None
    index = number - 1
    if not 0 <= index < len(recipes):
        return None
    row = recipes[index]
    return str(row.get("菜名") or row.get("name") or "") or None


def values(recipe: dict[str, Any], key: str) -> list[str]:
    raw = recipe.get(key) or []
    return [raw] if isinstance(raw, str) else [str(value) for value in raw if value]


def ingredient_names(recipe: dict[str, Any], key: str) -> list[str]:
    names = []
    for item in recipe.get(key) or []:
        if isinstance(item, dict):
            name = item.get("name")
        elif isinstance(item, (list, tuple)):
            name = item[0] if item else None
        else:
            name = item
        if name:
            names.append(str(name))
    return list(dict.fromkeys(names))


def display_name(recipe: dict[str, Any]) -> str:
    name = str(recipe.get("菜名") or recipe.get("name") or "")
    name = re.sub(r"#[^#]*#|【[^】]*】|\[[^\]]*\]", "", name)
    return name.strip()


def time_points(raw: str) -> int:
    if raw in TIME_POINTS:
        return TIME_POINTS[raw]
    # Unknown times receive a modest score, never an invented duration.
    return 8


def score_recipe(recipe: dict[str, Any], constraints: RecipeConstraints, request: RecipeRequest) -> float:
    name = str(recipe.get("菜名") or recipe.get("name") or "")
    main, aux = ingredient_names(recipe, "主食材"), ingredient_names(recipe, "辅料")
    score = 0.0
    main_hits = 0
    for wanted in constraints.required_ingredients:
        if any(wanted in item for item in main):
            score += 35
            main_hits += 1
        elif any(wanted in item for item in aux):
            score += 15
        if wanted in name:
            score += 25
    score += 30 * main_hits / max(len(main), 1)
    score += time_points(str(recipe.get("耗时") or "")) * (2 if request.prefer_quick else 1)
    instructions = str(recipe.get("做法") or "")
    steps = max(instructions.count(":") + instructions.count("："), 1)
    score += max(12 - steps, 0) * (2 if request.prefer_simple else 1)
    score += 18 * sum(flavor in values(recipe, "口味") for flavor in request.preferred_flavors)
    score += 10 * sum(method in values(recipe, "工艺") for method in request.preferred_methods)
    return score


def _canonical(text: str) -> str:
    return re.sub(r"\s+", "", text).replace("西红柿", "番茄").replace("番茄炒蛋", "番茄炒鸡蛋")


def _signature(recipe: dict[str, Any]) -> tuple:
    return (tuple(sorted(_canonical(name) for name in ingredient_names(recipe, "主食材"))),
            tuple(sorted(values(recipe, "工艺"))))


def rank_recipes(recipes: list[dict[str, Any]], constraints: RecipeConstraints,
                 request: RecipeRequest) -> list[dict[str, Any]]:
    # All hard constraints precede scoring, deduplication and the final count.
    candidates = [row for row in recipes if constraints.matches(row) and display_name(row)]
    candidates.sort(key=lambda row: (-score_recipe(row, constraints, request),
                                    str(row.get("菜名") or row.get("name") or "")))
    candidates = candidates[:CANDIDATE_LIMIT]
    unique = {}
    for row in candidates:
        unique.setdefault(_canonical(display_name(row)), row)
    pending = list(unique.values())
    selected, signatures, seen_methods = [], set(), set()
    while pending and len(selected) < request.limit:
        def adjusted(row):
            score = score_recipe(row, constraints, request)
            if _signature(row) in signatures:
                score -= 20
            if selected and set(values(row, "工艺")) - seen_methods:
                score += 6
            return (-score, display_name(row))
        row = min(pending, key=adjusted)
        pending.remove(row)
        reasons = []
        main = ingredient_names(row, "主食材")
        wanted = [item for item in constraints.required_ingredients if any(item in name for name in main)]
        if wanted:
            reasons.append("指定食材用于主料：" + "、".join(wanted))
        elif constraints.required_ingredients:
            reasons.append("包含指定食材：" + "、".join(constraints.required_ingredients))
        if row.get("耗时"):
            reasons.append("记录耗时：" + str(row["耗时"]))
        preferred = [item for item in request.preferred_flavors if item in values(row, "口味")]
        if preferred:
            reasons.append("符合已表达的口味偏好：" + "、".join(preferred))
        if not reasons:
            reasons.append("符合本次筛选条件")
        selected.append({**row, "推荐理由": reasons})
        signatures.add(_signature(row))
        seen_methods.update(values(row, "工艺"))
    return selected


def cypher_ranking(request: RecipeRequest) -> tuple[str, dict[str, Any]]:
    """Preselect by the same base score before the bounded graph candidate LIMIT."""
    clause = """WITH dish,
  [(dish)-[:HAS_MAIN_INGREDIENT]->(i:Ingredient) | i.name] AS main_names,
  [(dish)-[:HAS_AUX_INGREDIENT]->(i:Ingredient) | i.name] AS aux_names,
  [(dish)-[:HAS_FLAVOR]->(f:Flavor) | f.name] AS flavor_names,
  [(dish)-[:USES_METHOD]->(m:CookingMethod) | m.name] AS method_names
WITH dish, main_names,
  size([wanted IN $ranking_ingredients WHERE any(i IN main_names WHERE i CONTAINS wanted)]) AS main_hits,
  reduce(score = 0.0, wanted IN $ranking_ingredients | score +
    CASE WHEN any(i IN main_names WHERE i CONTAINS wanted) THEN 35
         WHEN any(i IN aux_names WHERE i CONTAINS wanted) THEN 15 ELSE 0 END +
    CASE WHEN dish.name CONTAINS wanted THEN 25 ELSE 0 END) AS ingredient_score,
  coalesce($ranking_times[dish.cook_time], 8) * $ranking_time_weight AS time_score,
  size(split(replace(coalesce(dish.instructions, ''), '：', ':'), ':')) - 1 AS raw_steps,
  18 * size([f IN $ranking_flavors WHERE f IN flavor_names]) +
  10 * size([m IN $ranking_methods WHERE m IN method_names]) AS preference_score
WITH dish, ingredient_score +
  30.0 * main_hits / CASE WHEN size(main_names) = 0 THEN 1 ELSE size(main_names) END +
  time_score + preference_score + $ranking_step_weight *
    CASE WHEN raw_steps = 0 THEN 11 WHEN raw_steps < 12 THEN 12 - raw_steps ELSE 0 END AS ranking_score
ORDER BY ranking_score DESC, dish.name
LIMIT $limit"""
    return clause, {"ranking_times": TIME_POINTS, "ranking_time_weight": 2 if request.prefer_quick else 1,
                    "ranking_step_weight": 2 if request.prefer_simple else 1,
                    "ranking_flavors": request.preferred_flavors, "ranking_methods": request.preferred_methods}
