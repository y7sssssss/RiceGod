"""Report deterministic data coverage and integrity metrics for Graph V2."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ricegod.domain.graph_coverage import build_graph_coverage_report
from scripts.import_recipes_to_neo4j import DEFAULT_RECIPE_PATH, _load_recipes, build_import_rows


def render_summary(report: dict) -> str:
    entities = report["entities"]
    coverage = report["coverage"]
    quality = report["quality"]
    category = coverage["categorized_ingredients"]
    allergen = coverage["allergen_mapping_density"]
    substitution = coverage["substitutions"]
    return "\n".join([
        "Graph V2 coverage",
        f"- dishes: {entities['dishes']}",
        f"- canonical ingredients: {entities['canonical_ingredients']}",
        f"- effective aliases: {entities['ingredient_aliases']}",
        f"- categorized ingredients: {category['covered']}/{category['total']} ({category['rate']:.2%})",
        f"- allergen mappings: {allergen['mapped_ingredients']} ingredients / {allergen['mappings']} edges",
        f"- substitution edges: {substitution['edges']} across {substitution['ingredients']} ingredients",
        f"- self aliases: {quality['self_aliases']}",
        f"- dangling alias/category/allergen links: {quality['dangling_alias_targets']}/"
        f"{quality['dangling_category_links']}/{quality['dangling_allergen_links']}",
        f"- substitution edges without known source: {quality['substitution_edges_without_known_source']}",
    ])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipe-path", type=Path, default=DEFAULT_RECIPE_PATH)
    parser.add_argument("--output", type=Path, help="Optional JSON report path")
    args = parser.parse_args()

    report = build_graph_coverage_report(build_import_rows(_load_recipes(args.recipe_path)))
    print(render_summary(report))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
