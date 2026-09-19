"""Opt-in ranking checks on real Neo4j recipes; no LLM calls or database writes."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from ricegod.domain.recipe_ranking import build_recipe_request, rank_recipes, ingredient_names, values
from ricegod.infrastructure.recipe_graph_repository import RecipeGraphRepository
from ricegod.infrastructure.recipe_repository import RecipeRepository


CASES = [
    ("egg_default_three", "用鸡蛋推荐家常菜，不要香菜，不吃辣", 3, "鸡蛋"),
    ("potato_quick", "用土豆推荐简单快手家常菜", 3, "土豆"),
    ("chicken_one", "用鸡肉推荐一道家常菜，不要花生", 1, "鸡肉"),
    ("egg_five", "用鸡蛋推荐五道家常菜，不要香菜", 5, "鸡蛋"),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("docs/recipe_ranking_evaluation.json"))
    args = parser.parse_args()
    graph, local = RecipeGraphRepository(), RecipeRepository()
    report = {"verified_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Four hand-written ranking checks on real Neo4j and local recipes. Checks fields, counts and backend consistency; not overall recommendation quality or answer accuracy.",
        "cases": []}
    try:
        for case_id, question, expected_count, ingredient in CASES:
            constraints = graph.extract_constraints(question)
            request = build_recipe_request(question, constraints)
            baseline = graph.query_by_cypher(**{
                "cypher": (template := graph.build_constrained_cypher(constraints, expected_count)).cypher,
                "params": template.params})
            candidates = graph.query_ranked_candidates(constraints, request)
            ranked = rank_recipes(candidates, constraints, request)
            fallback = local.search_recipes(question, constraints=constraints, request=request)
            names = [row["菜名"] for row in ranked]
            checks = {"expected_count": len(ranked) == expected_count,
                      "hard_constraints": bool(ranked) and all(constraints.matches(row) for row in ranked),
                      "backend_order_consistent": names == [row["name"] for row in fallback]}
            case = {"id": case_id, "question": question, "checks": checks,
                    "constraints": constraints.model_dump(), "request": request.model_dump(),
                    "baseline": [{"name": row["菜名"], "time": row["耗时"]} for row in baseline],
                    "ranked": [{"name": row["菜名"], "time": row["耗时"],
                        "method": values(row, "工艺"), "ingredient_in_main": any(ingredient in item
                            for item in ingredient_names(row, "主食材")), "reasons": row["推荐理由"]} for row in ranked]}
            report["cases"].append(case)
            print(json.dumps(case, ensure_ascii=False), flush=True)
        report["passed"] = all(all(case["checks"].values()) for case in report["cases"])
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0 if report["passed"] else 1
    finally:
        graph.client.close()


if __name__ == "__main__":
    raise SystemExit(main())
