"""Conservative rules for requests whose task structure is already explicit."""

import re


DEPENDENT_RECIPE_WORDS = ("这道菜", "这些菜", "所推荐", "它们", "它")
_COMPLEX_WORDS = ("分别", "各自", "对比", "比较", "区别", "另一", "另外", "第一组", "第二组", "换成", "替换")
_RECIPE_ACTION = re.compile(r"推荐|怎么做|如何做|能做什么|做哪些|找几道")
_DISH_COUNT = re.compile(r"(?<![第\d一二两三四五六七八九十])(?:\d+|[一二两三四五六七八九十]{1,3})\s*道")


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
        # Unknown recipe requests may need decomposition or query rewriting.
        if _RECIPE_ACTION.search(question) or catalog.extract_constraints(question, []).dish_name:
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
