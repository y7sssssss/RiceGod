"""Ground independent recipe conditions in their own spans of the user turn."""

import re

from ricegod.application.agents.state import QueryPlan
from ricegod.domain.recipe_ranking import build_recipe_request
from ricegod.application.performance import record_event


def _meal_spans(question: str) -> list[tuple[int, int]]:
    """Small deterministic fallback for explicitly separated meal requests."""
    matches = list(re.finditer(r"早餐|午餐|晚餐|早饭|午饭|晚饭", question))
    if len(matches) < 2 or len({match.group() for match in matches}) != len(matches):
        return []
    tail = re.search(r"(?:各|每餐|每顿)(?:推荐|给|做|要|来)", question[matches[-1].start():])
    end = matches[-1].start() + tail.start() if tail else len(question)
    spans = []
    for i, match in enumerate(matches):
        start, stop = match.start(), matches[i + 1].start() if i + 1 < len(matches) else end
        while stop > start and question[stop - 1] in "，,；;。 ":
            stop -= 1
        spans.append((start, stop))
    return spans


def meal_scopes(question: str) -> list[str]:
    return [question[start:end] for start, end in _meal_spans(question)]


def _exact_span(text: str, question: str) -> tuple[int, int] | None:
    if not text.strip():
        return None
    start = question.find(text)
    if start < 0 or question.find(text, start + 1) >= 0:
        return None
    return start, start + len(text)


def _ground_recipe_sources(recipes, question, meal_spans):
    """Repair only uniquely anchored source quotations; never guess by task order."""
    repaired = set()
    if meal_spans:
        used = set()
        for task in recipes:
            hits = []
            for evidence in (task.source_text, task.question):
                span = _exact_span(evidence, question)
                if span is not None:
                    hits = [i for i, (start, end) in enumerate(meal_spans)
                            if span[0] < end and start < span[1]]
                else:
                    # A rewritten task can retain one explicit meal label even
                    # when its original quote was the whole request.
                    labels = re.findall(r"早餐|午餐|晚餐|早饭|午饭|晚饭", evidence)
                    hits = [i for i, (start, end) in enumerate(meal_spans)
                            if labels and set(labels) == {question[start:start + 2]}]
                if len(hits) == 1:
                    break
            if len(hits) != 1 or hits[0] in used:
                raise ValueError("Recipe task must match one independent meal scope")
            used.add(hits[0])
            start, end = meal_spans[hits[0]]
            source = question[start:end]
            if task.source_text != source:
                repaired.add(task.id)
            task.source_text = source
        if len(used) != len(meal_spans):
            raise ValueError("Recipe tasks omit an independent meal scope")
    else:
        spans = [_exact_span(task.source_text, question) for task in recipes]
        valid = all(span is not None for span in spans) and not any(
            start < other_end and other_start < end
            for i, (start, end) in enumerate(spans)
            for other_start, other_end in spans[:i])
        if not valid:
            # Exact, distinct task questions may recover a bad model quote.
            replacements = [_exact_span(task.question, question) for task in recipes]
            if any(span is None for span in replacements):
                raise ValueError("Independent recipe tasks need exact user-text evidence")
            for i, (start, end) in enumerate(replacements):
                if any(start < other_end and other_start < end for other_start, other_end in replacements[:i]):
                    raise ValueError("Independent recipe scopes must not overlap")
            for task in recipes:
                if task.source_text != task.question:
                    repaired.add(task.id)
                task.source_text = task.question
            spans = replacements
        # A unique quote inside its own semicolon/sentence clause identifies
        # the full local request, including counts or exclusions omitted by a model.
        clauses = [(match.start(), match.end()) for match in re.finditer(r"[^；;。\n]+", question)]
        hits = [[i for i, (start, end) in enumerate(clauses) if start <= span[0] and span[1] <= end]
                for span in spans]
        if all(len(hit) == 1 for hit in hits) and len({hit[0] for hit in hits}) == len(recipes):
            for task, hit in zip(recipes, hits):
                start, end = clauses[hit[0]]
                source = question[start:end].strip()
                if task.source_text != source:
                    repaired.add(task.id)
                task.source_text = source
    if repaired:
        record_event("planner_scope_repair", task_count=len(repaired))


def ground_task_conditions(plan: QueryPlan, question: str, catalog, memories: list[dict],
                           referenced_name: str | None = None) -> QueryPlan:
    recipes = [task for task in plan.tasks if task.route == "recipe_query"]
    meal_spans = _meal_spans(question)
    scopes = [question[start:end] for start, end in meal_spans]
    if recipes and scopes and len(recipes) < len(scopes):
        raise ValueError("Separate meal requests must not be merged into one recipe task")
    if len(recipes) == 1:
        # Preserve quantities/restrictions even when the planner rewrites the question.
        recipes[0].source_text = question
    elif recipes:
        _ground_recipe_sources(recipes, question, meal_spans)

    shared_text = question
    for task in recipes:
        shared_text = shared_text.replace(task.source_text, " ", 1)
    shared = catalog.extract_constraints(shared_text, memories)
    # An explicitly stated allergy applies to every task, including when its
    # sentence was included in one task's source span by the planner.
    allergy_text = "，".join(clause for clause in re.split(r"[，,。；;\n]", question) if "过敏" in clause)
    allergy = catalog.extract_constraints(allergy_text, memories)
    shared.excluded_ingredients = list(dict.fromkeys([
        *shared.excluded_ingredients, *allergy.excluded_ingredients]))

    for task in recipes:
        local = catalog.extract_constraints(task.source_text, memories)
        for key in ("required_ingredients", "excluded_ingredients", "flavors", "methods", "dish_types"):
            setattr(local, key, list(dict.fromkeys([*getattr(local, key), *getattr(shared, key)])))
        local.avoid_spicy = local.avoid_spicy or shared.avoid_spicy
        local.dish_name = referenced_name or local.dish_name or shared.dish_name
        if len(recipes) == 1 and not local.dish_name:
            # A mixed recipe/culture turn can name two different dishes. The
            # recipe task may disambiguate one, but it must occur in user text.
            named = catalog.extract_constraints(task.question, []).dish_name
            if named and named in task.source_text:
                local.dish_name = named
        task.constraints = local
        request = build_recipe_request(task.source_text, local, memories=memories,
                                       flavors=catalog.flavors, methods=catalog.methods)
        common_request = build_recipe_request(shared_text, local)
        if request.requested_count is None and common_request.requested_count is not None:
            request.limit = common_request.limit
            request.requested_count = common_request.requested_count
        request.prefer_quick |= common_request.prefer_quick
        request.prefer_simple |= common_request.prefer_simple
        request.detailed |= common_request.detailed
        request.recommendation |= common_request.recommendation
        task.recipe_request = request

    # Reject a split that silently drops a known explicit positive condition.
    original = catalog.extract_constraints(question, [])
    if len(recipes) > 1:
        for key in ("required_ingredients", "flavors", "methods", "dish_types"):
            covered = {value for task in recipes for value in getattr(task.constraints, key)}
            if set(getattr(original, key)) - covered:
                raise ValueError("Recipe scopes omit an explicit condition")
    # Compatibility for existing single-recipe consumers; multi-task conditions
    # are always on PlannedTask, while this field represents shared conditions.
    plan.constraints = recipes[0].constraints if len(recipes) == 1 else shared
    plan.recipe_request = build_recipe_request(question, plan.constraints, memories=memories,
                                               flavors=catalog.flavors, methods=catalog.methods)
    return plan
