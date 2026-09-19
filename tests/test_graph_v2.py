from ricegod.domain.graph_v2 import (
    allergens_for_ingredient, canonical_ingredient, categories_for_ingredient,
    mentioned_allergens, substitution_rows,
)
from ricegod.domain.recipes import RecipeConstraints


def test_aliases_categories_and_allergens_are_deterministic():
    assert canonical_ingredient(" 番茄 ") == "西红柿"
    assert canonical_ingredient("蛋液") == "鸡蛋"
    assert categories_for_ingredient("番茄") == ["茄果类蔬菜"]
    assert allergens_for_ingredient("蛋液") == ["蛋类"]


def test_allergens_require_an_explicit_restriction():
    assert mentioned_allergens("我对蛋类过敏") == ["蛋类"]
    assert mentioned_allergens("我喜欢鸡蛋") == []


def test_local_fallback_uses_same_alias_and_allergen_rules():
    recipe = {"name": "番茄炒蛋", "主食材": [["番茄", "1个"], ["蛋液", "2个"]], "辅料": []}
    assert RecipeConstraints(required_ingredients=["西红柿"]).matches(recipe)
    assert not RecipeConstraints(excluded_allergens=["蛋类"]).matches(recipe)


def test_substitution_edges_are_bidirectional_and_provenanced():
    rows = substitution_rows()
    pairs = {(row["from"], row["to"]) for row in rows}
    assert ("玉米淀粉", "马铃薯淀粉") in pairs
    assert ("马铃薯淀粉", "玉米淀粉") in pairs
    assert all(row["source_id"] for row in rows)
