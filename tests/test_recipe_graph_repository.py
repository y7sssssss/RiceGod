import pytest

from ricegod.infrastructure.recipe_graph_repository import RecipeGraphRepository


def test_predefined_cypher_matches_recipe_steps():
    repository = RecipeGraphRepository()

    request = repository.build_predefined_cypher("香肠炒菜干怎么做")

    assert request is not None
    assert request.source == "predefined_recipe_steps"
    assert request.params == {"dish_name": "香肠炒菜干"}


def test_predefined_cypher_matches_recipe_ingredients():
    repository = RecipeGraphRepository()

    request = repository.build_predefined_cypher("香肠炒菜干需要什么食材")

    assert request is not None
    assert request.source == "predefined_recipe_ingredients"
    assert request.params == {"dish_name": "香肠炒菜干"}


def test_predefined_cypher_prefers_flavor_and_type_over_generic_ingredient():
    repository = RecipeGraphRepository()

    request = repository.build_predefined_cypher("有什么酱香的热菜")

    assert request is not None
    assert request.source == "predefined_by_flavor_and_type"
    assert request.params == {"flavor": "酱香", "dish_type": "热菜"}


def test_validate_read_only_cypher_rejects_write_queries():
    with pytest.raises(ValueError):
        RecipeGraphRepository.validate_read_only_cypher("MATCH (n) DELETE n")


def test_clean_cypher_removes_markdown_fence():
    cypher = RecipeGraphRepository.clean_cypher("```cypher\nMATCH (n) RETURN n LIMIT 1\n```")

    assert cypher == "MATCH (n) RETURN n LIMIT 1"


def test_graph_v2_alias_query_uses_canonical_ingredient():
    repository = RecipeGraphRepository()
    request = repository.build_predefined_cypher("用番茄推荐一道菜")
    assert request is not None
    assert request.params["ingredient"] == "西红柿"


def test_graph_v2_allergen_filter_is_a_graph_traversal():
    repository = RecipeGraphRepository()
    constraints = repository.extract_constraints("我对蛋类过敏，推荐一道清淡的菜")
    request = repository.build_constrained_cypher(constraints)
    assert constraints.excluded_allergens == ["蛋类"]
    assert "CONTAINS_ALLERGEN" in request.cypher
    assert request.params["excluded_allergens"] == ["蛋类"]


def test_graph_v2_builds_similarity_and_substitution_queries():
    repository = RecipeGraphRepository()
    similar = repository.build_predefined_cypher("找一些和香肠炒菜干类似的菜")
    substitute = repository.build_predefined_cypher("玉米淀粉可以用什么替代")
    assert similar is not None and similar.source == "graph_v2_similar_recipes"
    assert "shared_ingredients" in similar.cypher and similar.params["dish_name"] == "香肠炒菜干"
    assert substitute is not None and substitute.source == "graph_v2_substitutions"
    assert substitute.params["ingredient"] == "玉米淀粉"
