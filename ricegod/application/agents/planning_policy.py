"""Conservative rules for requests whose task structure is already explicit."""

import re

from ricegod.application.agents.recommendations import is_recipe_selection


DEPENDENT_RECIPE_WORDS = ("这道菜", "这些菜", "所推荐", "它们", "它")
_COMPLEX_WORDS = ("分别", "各自", "对比", "比较", "区别", "另一", "另外", "第一组", "第二组", "换成", "替换")
_RECIPE_ACTION = re.compile(r"推荐|怎么做|如何做|能做什么|做哪些|找几道")
_GRAPH_OPERATION = re.compile(r"相似|类似|差不多|同类|相近|替代|代替|替换|平替|换掉|换成")
_DISH_COUNT = re.compile(r"(?<![第\d一二两三四五六七八九十])(?:\d+|[一二两三四五六七八九十]{1,3})\s*道")
GENERAL_KEYWORDS = ("你好", "谢谢", "感谢", "你是谁", "hello", "hi")
RECIPE_KEYWORDS = ("怎么做", "如何做", "做法", "步骤", "食材", "配料", "材料", "推荐", "家常菜",
                   "能做什么", "做哪些", "相似", "类似", "同类", "相近", "替代", "代替", "替换",
                   "平替", "换掉", "换成", "炒", "煮", "炖", "蒸")
CULTURE_KEYWORDS = ("历史", "来历", "由来", "典故", "文化", "菜系", "特点", "发展", "起源", "故事")
VAGUE_RECIPE_PATTERNS = ("想做菜", "帮我看看", "推荐一个", "吃什么", "这个怎么做")
NEGATIVE_INGREDIENT_WORDS = ("不吃", "不能吃", "不喜欢", "不要", "不放", "不加", "避免", "忌口", "排除")


def ingredient_constraint_conflict(question, catalog):
    """Return a known ingredient requested in one clause and excluded in another."""
    meals = re.findall(r"早餐|午餐|晚餐|早饭|午饭|晚饭", question)
    if len(set(meals)) > 1:
        # Different meal scopes own independent conditions; grounding validates
        # each scope instead of treating the whole turn as contradictory.
        return None
    constraints = catalog.extract_constraints(question, [])
    if not constraints.excluded_ingredients:
        return None
    clauses = [clause.strip() for clause in re.split(r"[，,。；;\n]|但是|不过|但", question)
               if clause.strip()]
    for ingredient in constraints.excluded_ingredients:
        positive = False
        for clause in clauses:
            position = clause.find(ingredient)
            if position < 0:
                continue
            before = clause[:position]
            after = clause[position + len(ingredient):]
            if (not any(word in before for word in NEGATIVE_INGREDIENT_WORDS)
                    and "过敏" not in after):
                positive = True
                break
        if positive:
            return ingredient
    return None


def deterministic_routes(question, catalog):
    """Return high-confidence task routes, or None when the planner must classify."""
    text = question.strip()
    if not text:
        return ["additional_query"]
    if is_recipe_selection(text):
        return ["recipe_query"]
    if ingredient_constraint_conflict(text, catalog):
        return ["additional_query"]
    lowered = text.lower()
    constraints = catalog.extract_constraints(text, [])
    has_entity = bool(constraints.dish_name or constraints.required_ingredients or constraints.flavors
                      or constraints.methods or constraints.dish_types)
    is_culture = any(word in text for word in CULTURE_KEYWORDS)
    is_recipe = any(word in text for word in RECIPE_KEYWORDS) or (has_entity and not is_culture)
    if any(pattern in text for pattern in VAGUE_RECIPE_PATTERNS) and not has_entity and not is_culture:
        return ["additional_query"]
    routes = []
    if is_recipe:
        routes.append("recipe_query")
    if is_culture:
        routes.append("food_knowledge")
    if routes:
        return routes
    if any(word in lowered for word in GENERAL_KEYWORDS):
        return ["general_query"]
    return None


def _multiple_known_dishes(question, catalog):
    # Longest names first: a short dish name inside a longer one is not another request.
    spans = []
    for name in sorted(getattr(catalog, "recipe_names", []), key=len, reverse=True):
        if not name:
            continue
        start = question.find(name)
        if start < 0:
            continue
        end = start + len(name)
        if any(start < other_end and other_start < end for other_start, other_end in spans):
            continue
        spans.append((start, end))
        if len(spans) > 1:
            return True
    return False


def _multiple_known_ingredients(question, catalog):
    remaining = question
    found = []
    for name in sorted(getattr(catalog, "ingredients", []), key=len, reverse=True):
        if name and name in remaining:
            found.append(name)
            remaining = remaining.replace(name, " " * len(name))
        if len(found) > 1:
            return True
    return False


def deterministic_plan_reason(question, routes, scopes, catalog):
    """Return a reason only when one task or bounded meal tasks suffice."""
    route_set = set(routes)
    if not route_set & {"recipe_query", "food_knowledge"}:
        return "no_domain_intent"
    if any(word in question for word in _COMPLEX_WORDS):
        return None
    if route_set == {"recipe_query"}:
        if scopes:
            return "explicit_meals" if len(scopes) <= 4 else None
        if len(_RECIPE_ACTION.findall(question)) > 1 or len(_DISH_COUNT.findall(question)) > 1:
            return None
        if _multiple_known_dishes(question, catalog):
            return None
        if any(word in question for word in ("替代", "代替", "替换", "平替", "换掉", "换成")):
            if _multiple_known_ingredients(question, catalog):
                return None
        # Unknown recipe requests may need decomposition or query rewriting.
        constraints = catalog.extract_constraints(question, [])
        if (_RECIPE_ACTION.search(question) or _GRAPH_OPERATION.search(question)
                or constraints.dish_name):
            return "single_recipe"
    elif route_set == {"food_knowledge"}:
        clauses = [clause for clause in re.split(r"[，,；;。！？?\n]|以及|并且", question) if clause.strip()]
        if len(clauses) == 1 and not _multiple_known_dishes(question, catalog):
            return "single_culture"
    elif route_set == {"recipe_query", "food_knowledge"}:
        if (any(word in question for word in DEPENDENT_RECIPE_WORDS)
                and len(_RECIPE_ACTION.findall(question)) <= 1
                and len(_DISH_COUNT.findall(question)) <= 1
                and not scopes and not _multiple_known_dishes(question, catalog)):
            return "dependent_lookup"
    return None
