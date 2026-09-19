"""Deterministic data-quality metrics for the recipe knowledge graph."""

from __future__ import annotations

from typing import Any


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def build_graph_coverage_report(rows: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    dishes = {row["name"] for row in rows["dishes"]}
    main_dishes = {row["dish_name"] for row in rows["main_ingredients"]}
    aux_dishes = {row["dish_name"] for row in rows["aux_ingredients"]}
    ingredient_names = {
        row["ingredient_name"]
        for key in ("main_ingredients", "aux_ingredients")
        for row in rows[key]
    }
    aliases = rows["aliases"]
    categorized = {row["ingredient_name"] for row in rows["ingredient_categories"]}
    allergenic = {row["ingredient_name"] for row in rows["ingredient_allergens"]}
    category_names = {
        value
        for row in rows["category_parents"]
        for value in (row["child"], row["parent"])
    } | {row["category"] for row in rows["ingredient_categories"]}
    allergen_names = {row["allergen"] for row in rows["ingredient_allergens"]}
    source_ids = {row["id"] for row in rows["sources"]}
    substitution_ingredients = {
        value
        for row in rows["substitutions"]
        for value in (row["from"], row["to"])
    }

    dish_fields = {
        "main_ingredient": main_dishes,
        "any_ingredient": main_dishes | aux_dishes,
        "flavor": {row["dish_name"] for row in rows["flavors"]},
        "method": {row["dish_name"] for row in rows["methods"]},
        "dish_type": {row["dish_name"] for row in rows["types"]},
        "instructions": {row["name"] for row in rows["dishes"] if row.get("instructions")},
    }

    return {
        "entities": {
            "dishes": len(dishes),
            "canonical_ingredients": len(ingredient_names),
            "ingredient_aliases": len(aliases),
            "ingredient_categories": len(category_names),
            "allergens": len(allergen_names),
            "sources": len(source_ids),
        },
        "coverage": {
            "categorized_ingredients": {
                "covered": len(categorized),
                "total": len(ingredient_names),
                "rate": _rate(len(categorized), len(ingredient_names)),
            },
            # This is mapping density, not allergen recall: most ingredients are not allergens.
            "allergen_mapping_density": {
                "mapped_ingredients": len(allergenic),
                "total_ingredients": len(ingredient_names),
                "rate": _rate(len(allergenic), len(ingredient_names)),
                "mappings": len(rows["ingredient_allergens"]),
            },
            "dish_fields": {
                field: {
                    "covered": len(covered),
                    "total": len(dishes),
                    "rate": _rate(len(covered), len(dishes)),
                }
                for field, covered in dish_fields.items()
            },
            "substitutions": {
                "edges": len(rows["substitutions"]),
                "ingredients": len(substitution_ingredients),
                "source_complete": sum(
                    bool(row.get("source_id")) and row["source_id"] in source_ids
                    for row in rows["substitutions"]
                ),
            },
        },
        "quality": {
            "self_aliases": sum(row["name"] == row["canonical_name"] for row in aliases),
            "dangling_alias_targets": sum(row["canonical_name"] not in ingredient_names for row in aliases),
            "dangling_category_links": sum(
                row["ingredient_name"] not in ingredient_names for row in rows["ingredient_categories"]
            ),
            "dangling_allergen_links": sum(
                row["ingredient_name"] not in ingredient_names for row in rows["ingredient_allergens"]
            ),
            "substitution_endpoints_absent_from_recipes": len(substitution_ingredients - ingredient_names),
            "substitution_edges_without_known_source": sum(
                not row.get("source_id") or row["source_id"] not in source_ids
                for row in rows["substitutions"]
            ),
        },
    }
