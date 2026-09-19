import argparse
import json
from pathlib import Path
from typing import Any

from ricegod.infrastructure.neo4j_client import Neo4jClient
from ricegod.domain.graph_v2 import (
    allergens_for_ingredient, canonical_ingredient, categories_for_ingredient,
    graph_v2_config, substitution_rows,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RECIPE_PATH = PROJECT_ROOT / "data" / "recipe.json"


DEFAULT_BATCH_SIZE = 500


def _ingredient_parts(item: Any) -> tuple[str, str]:
    if isinstance(item, list) and item:
        name = str(item[0]).strip()
        amount = str(item[1]).strip() if len(item) > 1 else ""
        return name, amount
    return str(item).strip(), ""


def _load_recipes(path: Path) -> dict[str, dict[str, Any]]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Recipe data must be a JSON object: {path}")
    return data


def _batched(rows: list[dict[str, Any]], batch_size: int) -> list[list[dict[str, Any]]]:
    return [rows[index : index + batch_size] for index in range(0, len(rows), batch_size)]


def _print_progress(label: str, current: int, total: int) -> None:
    print(f"{label} {current}/{total}", flush=True)


def build_import_rows(recipes: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    rows: dict[str, list[dict[str, Any]]] = {
        "dishes": [],
        "main_ingredients": [],
        "aux_ingredients": [],
        "flavors": [],
        "methods": [],
        "types": [],
        "aliases": [],
        "ingredient_categories": [],
        "category_parents": [],
        "ingredient_allergens": [],
        "substitutions": substitution_rows(),
        "sources": graph_v2_config().get("sources", []),
    }

    for name, recipe in recipes.items():
        rows["dishes"].append(
            {
                "name": name,
                "cook_time": recipe.get("耗时") or "",
                "instructions": recipe.get("做法") or "",
            }
        )

        for item in recipe.get("主食材") or []:
            ingredient_name, amount_text = _ingredient_parts(item)
            if ingredient_name:
                canonical_name = canonical_ingredient(ingredient_name)
                rows["main_ingredients"].append(
                    {
                        "dish_name": name,
                        "ingredient_name": canonical_name,
                        "raw_name": ingredient_name,
                        "amount_text": amount_text,
                    }
                )
                _append_enrichment_rows(rows, ingredient_name, canonical_name)

        for item in recipe.get("辅料") or []:
            ingredient_name, amount_text = _ingredient_parts(item)
            if ingredient_name:
                canonical_name = canonical_ingredient(ingredient_name)
                rows["aux_ingredients"].append(
                    {
                        "dish_name": name,
                        "ingredient_name": canonical_name,
                        "raw_name": ingredient_name,
                        "amount_text": amount_text,
                    }
                )
                _append_enrichment_rows(rows, ingredient_name, canonical_name)

        for row_key, recipe_key in (
            ("flavors", "口味"),
            ("methods", "工艺"),
            ("types", "类型"),
        ):
            value = recipe.get(recipe_key)
            if value:
                rows[row_key].append({"dish_name": name, "value": value})

    for key in ("aliases", "ingredient_categories", "category_parents", "ingredient_allergens", "substitutions"):
        rows[key] = _unique_dicts(rows[key])
    return rows


def _append_enrichment_rows(rows: dict[str, list[dict[str, Any]]], raw_name: str, canonical_name: str) -> None:
    if raw_name != canonical_name:
        rows["aliases"].append({"name": raw_name, "canonical_name": canonical_name})
    for category in categories_for_ingredient(canonical_name):
        rows["ingredient_categories"].append({"ingredient_name": canonical_name, "category": category})
    for allergen in allergens_for_ingredient(canonical_name):
        rows["ingredient_allergens"].append({
            "ingredient_name": canonical_name,
            "allergen": allergen,
            "source_id": "fda-major-allergens",
        })
    for child, parent in graph_v2_config().get("category_parents", {}).items():
        rows["category_parents"].append({"child": child, "parent": parent})


def _unique_dicts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    unique = []
    for row in rows:
        marker = json.dumps(row, ensure_ascii=False, sort_keys=True)
        if marker not in seen:
            seen.add(marker)
            unique.append(row)
    return unique


def create_constraints(client: Neo4jClient) -> None:
    print("Creating constraints...", flush=True)
    statements = [
        "CREATE CONSTRAINT dish_name IF NOT EXISTS FOR (n:Dish) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT ingredient_name IF NOT EXISTS FOR (n:Ingredient) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT flavor_name IF NOT EXISTS FOR (n:Flavor) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT method_name IF NOT EXISTS FOR (n:CookingMethod) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT type_name IF NOT EXISTS FOR (n:DishType) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT alias_name IF NOT EXISTS FOR (n:IngredientAlias) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT category_name IF NOT EXISTS FOR (n:IngredientCategory) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT allergen_name IF NOT EXISTS FOR (n:Allergen) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT source_id IF NOT EXISTS FOR (n:Source) REQUIRE n.id IS UNIQUE",
    ]
    for statement in statements:
        client.run_query(statement)
    print("Constraints are ready.", flush=True)


def clear_graph(client: Neo4jClient) -> None:
    print("Clearing existing Neo4j graph...", flush=True)
    client.run_query("MATCH (n) DETACH DELETE n")
    print("Existing Neo4j graph cleared.", flush=True)


def _run_batches(
    client: Neo4jClient,
    label: str,
    cypher: str,
    rows: list[dict[str, Any]],
    batch_size: int,
) -> None:
    total = len(rows)
    if total == 0:
        _print_progress(label, 0, 0)
        return

    imported = 0
    for batch in _batched(rows, batch_size):
        client.run_query(cypher, {"rows": batch})
        imported += len(batch)
        _print_progress(label, imported, total)


def import_recipes(
    client: Neo4jClient,
    recipes: dict[str, dict[str, Any]],
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> None:
    rows = build_import_rows(recipes)

    _run_batches(
        client,
        "Imported dishes",
        """
UNWIND $rows AS row
MERGE (dish:Dish {name: row.name})
SET dish.cook_time = row.cook_time,
    dish.instructions = row.instructions,
    dish.graph_version = '2.0'
""",
        rows["dishes"],
        batch_size,
    )

    _run_batches(
        client,
        "Imported main ingredient relations",
        """
UNWIND $rows AS row
MATCH (dish:Dish {name: row.dish_name})
MERGE (ingredient:Ingredient {name: row.ingredient_name})
MERGE (dish)-[rel:HAS_MAIN_INGREDIENT {raw_name: row.raw_name}]->(ingredient)
SET rel.amount_text = row.amount_text
""",
        rows["main_ingredients"],
        batch_size,
    )

    _run_batches(
        client,
        "Imported auxiliary ingredient relations",
        """
UNWIND $rows AS row
MATCH (dish:Dish {name: row.dish_name})
MERGE (ingredient:Ingredient {name: row.ingredient_name})
MERGE (dish)-[rel:HAS_AUX_INGREDIENT {raw_name: row.raw_name}]->(ingredient)
SET rel.amount_text = row.amount_text
""",
        rows["aux_ingredients"],
        batch_size,
    )

    _run_property_batches(
        client=client,
        label="Imported flavor relations",
        node_label="Flavor",
        relationship="HAS_FLAVOR",
        rows=rows["flavors"],
        batch_size=batch_size,
    )

    _run_batches(client, "Imported ingredient aliases", """
UNWIND $rows AS row
MERGE (alias:IngredientAlias {name: row.name})
MERGE (ingredient:Ingredient {name: row.canonical_name})
MERGE (alias)-[:ALIAS_OF]->(ingredient)
""", rows["aliases"], batch_size)
    _run_batches(client, "Imported ingredient categories", """
UNWIND $rows AS row
MERGE (ingredient:Ingredient {name: row.ingredient_name})
MERGE (category:IngredientCategory {name: row.category})
MERGE (ingredient)-[:IN_CATEGORY]->(category)
""", rows["ingredient_categories"], batch_size)
    _run_batches(client, "Imported category hierarchy", """
UNWIND $rows AS row
MERGE (child:IngredientCategory {name: row.child})
MERGE (parent:IngredientCategory {name: row.parent})
MERGE (child)-[:IS_A]->(parent)
""", rows["category_parents"], batch_size)
    _run_batches(client, "Imported allergen relations", """
UNWIND $rows AS row
MERGE (ingredient:Ingredient {name: row.ingredient_name})
MERGE (allergen:Allergen {name: row.allergen})
MERGE (source:Source {id: row.source_id})
MERGE (ingredient)-[rel:CONTAINS_ALLERGEN]->(allergen)
SET rel.source_id = row.source_id
MERGE (allergen)-[:SUPPORTED_BY]->(source)
""", rows["ingredient_allergens"], batch_size)
    _run_batches(client, "Imported substitutions", """
UNWIND $rows AS row
MERGE (sourceIngredient:Ingredient {name: row.from})
MERGE (targetIngredient:Ingredient {name: row.to})
MERGE (source:Source {id: row.source_id})
MERGE (sourceIngredient)-[rel:CAN_SUBSTITUTE {context: row.context}]->(targetIngredient)
SET rel.score = row.score, rel.source_id = row.source_id
MERGE (targetIngredient)-[:SUPPORTED_BY]->(source)
""", rows["substitutions"], batch_size)
    _run_batches(client, "Imported sources", """
UNWIND $rows AS row
MERGE (source:Source {id: row.id})
SET source.name = row.name, source.url = row.url, source.kind = row.kind
""", rows["sources"], batch_size)
    _run_batches(client, "Linked dishes to source", """
UNWIND $rows AS row
MATCH (dish:Dish {name: row.name})
MATCH (source:Source {id: 'recipe-json-v1'})
MERGE (dish)-[:SUPPORTED_BY]->(source)
""", rows["dishes"], batch_size)
    _run_property_batches(
        client=client,
        label="Imported cooking method relations",
        node_label="CookingMethod",
        relationship="USES_METHOD",
        rows=rows["methods"],
        batch_size=batch_size,
    )
    _run_property_batches(
        client=client,
        label="Imported dish type relations",
        node_label="DishType",
        relationship="BELONGS_TO_TYPE",
        rows=rows["types"],
        batch_size=batch_size,
    )


def _run_property_batches(
    client: Neo4jClient,
    label: str,
    node_label: str,
    relationship: str,
    rows: list[dict[str, Any]],
    batch_size: int,
) -> None:
    _run_batches(
        client,
        label,
        f"""
UNWIND $rows AS row
MATCH (dish:Dish {{name: row.dish_name}})
MERGE (target:{node_label} {{name: row.value}})
MERGE (dish)-[:{relationship}]->(target)
""",
        rows,
        batch_size,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Import recipe JSON into Neo4j.")
    parser.add_argument("--recipe-path", default=str(DEFAULT_RECIPE_PATH))
    parser.add_argument("--clear", action="store_true", help="Clear existing Neo4j graph before import.")
    parser.add_argument(
        "--batch-size",
        default=DEFAULT_BATCH_SIZE,
        type=int,
        help=f"Number of rows to write per Neo4j batch. Default: {DEFAULT_BATCH_SIZE}.",
    )
    args = parser.parse_args()

    recipes = _load_recipes(Path(args.recipe_path))
    if args.batch_size <= 0:
        raise ValueError("--batch-size must be greater than 0.")

    print(f"Loaded recipes: {len(recipes)}", flush=True)
    print(f"Batch size: {args.batch_size}", flush=True)

    client = Neo4jClient()
    try:
        if args.clear:
            clear_graph(client)
        create_constraints(client)
        import_recipes(client, recipes, batch_size=args.batch_size)
        dish_count = client.run_query("MATCH (dish:Dish) RETURN count(dish) AS count")
        ingredient_count = client.run_query("MATCH (ingredient:Ingredient) RETURN count(ingredient) AS count")
        main_rel_count = client.run_query(
            "MATCH (:Dish)-[rel:HAS_MAIN_INGREDIENT]->(:Ingredient) RETURN count(rel) AS count"
        )
        print(f"Imported dishes: {dish_count[0]['count'] if dish_count else 0}")
        print(f"Imported ingredients: {ingredient_count[0]['count'] if ingredient_count else 0}")
        print(f"Imported main ingredient relations: {main_rel_count[0]['count'] if main_rel_count else 0}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
