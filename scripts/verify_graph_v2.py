"""Run a small acceptance check against the imported Graph V2 database."""

from __future__ import annotations

import json
from typing import Any

from ricegod.infrastructure.neo4j_client import Neo4jClient
from ricegod.infrastructure.recipe_graph_repository import RecipeGraphRepository


REQUIRED_COUNTS = {
    "Dish": 1,
    "Ingredient": 1,
    "IngredientAlias": 1,
    "Allergen": 9,
    "Source": 3,
    "ALIAS_OF": 1,
    "CONTAINS_ALLERGEN": 1,
    "CAN_SUBSTITUTE": 1,
}


def _single_count(client: Neo4jClient, query: str, name: str) -> int:
    rows = client.run_query(query, read_only=True, max_records=1, timeout=10.0)
    if not rows:
        raise RuntimeError(f"Neo4j did not return a count for {name}.")
    return int(rows[0]["count"])


def collect_report(client: Neo4jClient) -> dict[str, Any]:
    repository = RecipeGraphRepository(client=client)
    counts: dict[str, int] = {}
    for name in REQUIRED_COUNTS:
        if name.isupper():
            query = f"MATCH ()-[r:{name}]->() RETURN count(r) AS count"
        else:
            query = f"MATCH (n:{name}) RETURN count(n) AS count"
        counts[name] = _single_count(client, query, name)

    alias = client.run_query(
        """
MATCH (:IngredientAlias {name: $alias})-[:ALIAS_OF]->(ingredient:Ingredient)
RETURN ingredient.name AS canonical
""",
        {"alias": "番茄"},
        read_only=True,
        max_records=1,
        timeout=10.0,
    )
    substitutions = repository.find_ingredient_substitutes("玉米淀粉", limit=3)
    similar = repository.find_similar_recipes("香肠炒菜干", limit=3)

    constraints = repository.extract_constraints("我对蛋类过敏，推荐一道清淡的菜")
    allergen_request = repository.build_constrained_cypher(constraints, limit=5)
    safe_recipes = client.run_query(
        allergen_request.cypher,
        allergen_request.params,
        read_only=True,
        max_records=5,
        timeout=10.0,
    )

    return {
        "counts": counts,
        "alias_example": alias,
        "substitution_example": substitutions,
        "similar_recipe_example": similar,
        "allergen_filter": {
            "excluded_allergens": constraints.excluded_allergens,
            "result_count": len(safe_recipes),
            "recipes": [row["菜名"] for row in safe_recipes],
        },
    }


def validate_report(report: dict[str, Any]) -> None:
    for name, minimum in REQUIRED_COUNTS.items():
        actual = report["counts"][name]
        if actual < minimum:
            raise RuntimeError(f"Graph V2 check failed: {name}={actual}, expected >= {minimum}.")
    if report["alias_example"] != [{"canonical": "西红柿"}]:
        raise RuntimeError("Graph V2 check failed: 番茄 did not resolve to 西红柿.")
    if not report["substitution_example"]:
        raise RuntimeError("Graph V2 check failed: no substitution was returned for 玉米淀粉.")
    if not report["similar_recipe_example"]:
        raise RuntimeError("Graph V2 check failed: no similar recipe was returned.")
    if report["allergen_filter"]["excluded_allergens"] != ["蛋类"]:
        raise RuntimeError("Graph V2 check failed: the egg allergy constraint was not extracted.")


def main() -> None:
    client = Neo4jClient()
    try:
        report = collect_report(client)
        validate_report(report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print("Graph V2 verification passed.")
    finally:
        client.close()


if __name__ == "__main__":
    main()
