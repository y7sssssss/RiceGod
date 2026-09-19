"""Conversation recommendation snapshots and deterministic ordinal selection."""

import re
from dataclasses import dataclass

from ricegod.application.agents.state import QueryPlan, RecommendationGroup, TaskResult
from ricegod.domain.recipe_ranking import _number, build_recipe_request, display_name, recipe_reference_number


MEALS = {"早餐": ("早餐", "早饭"), "午餐": ("午餐", "午饭"), "晚餐": ("晚餐", "晚饭")}


def is_recipe_selection(question: str) -> bool:
    if recipe_reference_number(question) is None:
        return False
    if any(word in question for word in ("怎么做", "步骤", "做法", "详细", "材料", "配料", "我选", "我要", "就选", "选择")):
        return True
    return bool(re.fullmatch(r"(?:请|要|选|看一下|看看)?(?:早餐|早饭|午餐|午饭|晚餐|晚饭|第(?:\d+|[零〇一二两三四五六七八九十]{1,3})组)?"
                             r"(?:的)?第(?:\d+|[零〇一二两三四五六七八九十]{1,3})(?:道|个|款)(?:菜)?[吧。？?！!\s]*", question.strip()))


@dataclass
class RecipeSelection:
    name: str | None = None
    error_code: str | None = None
    clarification: str = ""


def select_recommendation(question: str, groups: list[RecommendationGroup]) -> RecipeSelection:
    if not groups:
        return RecipeSelection(error_code="recipe_selection_missing_list", clarification=
            "当前会话还没有可选择的推荐列表，请先让我推荐菜谱，或直接告诉我菜名。")
    if len(re.findall(r"第(?:\d+|[零〇一二两三四五六七八九十]{1,3})(?:道|个|款)", question)) > 1:
        return RecipeSelection(error_code="recipe_selection_multiple", clarification=
            "请先选择一组中的一道菜，例如“晚餐第二道菜怎么做”，我会按选择展开详情。")
    meals = [label for label, aliases in MEALS.items() if any(alias in question for alias in aliases)]
    group_number = re.search(r"第(\d+|[零〇一二两三四五六七八九十]{1,3})组", question)
    if group_number:
        index = _number(group_number.group(1)) - 1
        candidates = groups[index:index + 1] if 0 <= index < len(groups) else []
        if meals:
            candidates = [group for group in candidates if any(group.label.startswith(meal) for meal in meals)]
    elif meals:
        candidates = [group for group in groups if any(group.label.startswith(meal) for meal in meals)]
    else:
        candidates = groups
    labels = "、".join(group.label for group in groups)
    if not candidates:
        return RecipeSelection(error_code="recipe_selection_missing_group", clarification=
            f"上次推荐中没有你指定的分组，现有分组是：{labels}。请说明要看哪一组的哪道菜。")
    if len(candidates) != 1 or len(meals) > 1:
        return RecipeSelection(error_code="recipe_selection_ambiguous_group", clarification=
            f"上次推荐分为：{labels}。请明确选择哪一组，例如“第1组第一道菜怎么做”。")
    group = candidates[0]
    number = recipe_reference_number(question)
    if not group.records:
        return RecipeSelection(error_code="recipe_selection_empty_group", clarification=
            f"上次{group.label}没有可选择的菜谱，请重新告诉我条件让我推荐。")
    if number is None or not 1 <= number <= len(group.records):
        return RecipeSelection(error_code="recipe_selection_out_of_range", clarification=
            f"上次{group.label}只有 {len(group.records)} 道菜，请选择第 1 至第 {len(group.records)} 道，或直接告诉我菜名。")
    row = group.records[number - 1]
    return RecipeSelection(name=str(row.get("菜名") or row.get("name") or ""))


def new_recommendations(plan: QueryPlan, results: dict[str, TaskResult]) -> list[RecommendationGroup]:
    if plan.selected_recipe_name or plan.recipe_selection_error:
        return []
    tasks = [task for task in plan.tasks if task.route == "recipe_query" and
             (task.recipe_request or (plan.recipe_request if plan.recipe_request.query else
              build_recipe_request(task.question, task.constraints or plan.constraints))).recommendation]
    groups = []
    for i, task in enumerate(tasks):
        text = task.source_text or task.question
        label = next((meal for meal, aliases in MEALS.items() if any(alias in text for alias in aliases)),
                     "推荐" if len(tasks) == 1 else f"第{i + 1}组")
        result = results[task.id]
        rows = [row for row in result.records if row.get("菜名") or row.get("name")]
        groups.append(RecommendationGroup(task_id=task.id, label=label, question=text,
            records=rows if result.status in ("ok", "degraded") else []))
    labels = [group.label for group in groups]
    for i, group in enumerate(groups):
        if labels.count(group.label) > 1:
            group.label += f"（第{i + 1}组）"
    return groups if any(group.records for group in groups) else []


def recommendations_in_order(answer: str, groups: list[RecommendationGroup]) -> bool:
    cursor = 0
    for group in groups:
        for row in group.records:
            name = display_name(row)
            position = answer.find(name, cursor)
            if not name or position < 0:
                return False
            cursor = position + len(name)
    return True
