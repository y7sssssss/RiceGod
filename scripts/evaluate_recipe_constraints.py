"""Offline field-constraint evaluation; does not evaluate model answer quality."""

import argparse
import json
from pathlib import Path

from ricegod.infrastructure.recipe_graph_repository import RecipeGraphRepository
from ricegod.infrastructure.recipe_repository import RecipeRepository


CASES = [
    {
        "id": "allergy_and_dislike",
        "question": "用鸡蛋推荐家常菜，不要香菜",
        "memories": [{"type": "allergy", "content": "用户对花生过敏"}],
        "required": ["鸡蛋"], "excluded": ["香菜", "花生"], "type": "家常菜",
    },
    {
        "id": "remembered_dislike",
        "question": "用鸡肉推荐家常菜",
        "memories": [{"type": "disliked_ingredient", "content": "用户不吃香菜"}],
        "required": ["鸡肉"], "excluded": ["香菜"], "type": "家常菜",
    },
    {
        "id": "no_spicy",
        "question": "不吃辣，推荐家常菜",
        "memories": [], "required": [], "excluded": [], "type": "家常菜", "no_spicy": True,
    },
    {
        "id": "ingredient_flavor_type",
        "question": "用鸡肉推荐咸鲜热菜，不要花生",
        "memories": [], "required": ["鸡肉"], "excluded": ["花生"], "type": "热菜", "flavor": "咸鲜",
    },
    {
        "id": "two_required_ingredients",
        "question": "用土豆和鸡蛋推荐家常菜",
        "memories": [], "required": ["土豆", "鸡蛋"], "excluded": [], "type": "家常菜",
    },
]


def satisfies_expected_fields(row, case):
    # Ground truth is specified independently of the extracted RecipeConstraints.
    names = [str(item[0]) if isinstance(item, list) and item else str(item)
             for key in ("主食材", "辅料") for item in row.get(key, [])]
    if any(not any(ingredient in name for name in names) for ingredient in case["required"]):
        return False
    if any(banned in name for banned in case["excluded"] for name in names):
        return False
    if row.get("类型") != case["type"] or (case.get("flavor") and row.get("口味") != case["flavor"]):
        return False
    if case.get("no_spicy") and ("辣" in row.get("口味", "") or any(
        marker in name for name in names for marker in ("辣椒", "辣油", "辣酱", "泡椒", "剁椒", "小米椒")
    )):
        return False
    return True


def evaluate():
    catalog, local = RecipeGraphRepository(), RecipeRepository()
    cases = []
    for case in CASES:
        constraints = catalog.extract_constraints(case["question"], case["memories"])
        baseline = local.search_recipes(case["question"], limit=5)
        optimized = local.search_recipes(case["question"], limit=5, constraints=constraints)
        scores = {}
        for name, rows in (("baseline", baseline), ("optimized", optimized)):
            matched = sum(satisfies_expected_fields(row, case) for row in rows)
            scores[name] = {"returned": len(rows), "matched": matched,
                            "field_match_rate": matched / len(rows) if rows else None,
                            "dish_names": [row["name"] for row in rows]}
        cases.append({"id": case["id"], "question": case["question"],
                      "constraints": constraints.model_dump(), **scores})
    summary = {}
    for name in ("baseline", "optimized"):
        total = sum(case[name]["returned"] for case in cases)
        matched = sum(case[name]["matched"] for case in cases)
        summary[name] = {"returned": total, "matched": matched,
                         "field_match_rate": matched / total if total else None,
                         "cases_with_results": sum(bool(case[name]["returned"]) for case in cases)}
    return {"scope": "Five hand-written cases on local recipe fields; no LLM, memory extraction or database evaluation",
            "recipe_count": len(catalog.recipe_names), "summary": summary, "cases": cases}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate()
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
