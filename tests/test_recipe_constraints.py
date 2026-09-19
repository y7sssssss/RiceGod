import json

from ricegod.domain.recipes import RecipeConstraints, extract_constraints
from ricegod.infrastructure.recipe_graph_repository import RecipeGraphRepository
from ricegod.infrastructure.recipe_repository import RecipeRepository


def _catalog(tmp_path):
    path = tmp_path / "recipes.json"
    path.write_text(json.dumps({
        "香菜鸡蛋": {"主食材": [["鸡蛋", "2个"]], "辅料": [["香菜", "少许"]], "口味": "咸鲜", "工艺": "炒", "类型": "热菜"},
        "花生鸡蛋": {"主食材": [["鸡蛋", "2个"]], "辅料": [["花生", "少许"]], "口味": "咸鲜", "工艺": "炒", "类型": "热菜"},
        "清炒鸡蛋": {"主食材": [["鸡蛋", "2个"]], "辅料": [], "口味": "咸鲜", "工艺": "炒", "类型": "热菜"},
        "辣炒鸡蛋": {"主食材": [["鸡蛋", "2个"]], "辅料": [["辣椒", "少许"]], "口味": "香辣", "工艺": "炒", "类型": "热菜"},
        "咸鲜土豆": {"主食材": [["土豆", "1个"]], "辅料": [], "口味": "咸鲜", "工艺": "炒", "类型": "热菜"},
    }, ensure_ascii=False), encoding="utf-8")
    return RecipeGraphRepository(data_path=path), RecipeRepository(data_path=path)


def test_combined_ingredient_flavor_method_type_and_exclusions_are_all_applied(tmp_path):
    graph, local = _catalog(tmp_path)
    filters = graph.extract_constraints("用鸡蛋推荐咸鲜热菜，不要香菜", [{"type": "allergy", "content": "用户对花生过敏"}])
    filters.methods = ["炒"]
    request = graph.build_constrained_cypher(filters)
    assert request.params["required_ingredients"] == ["鸡蛋"]
    assert set(request.params["excluded_ingredients"]) == {"香菜", "花生"}
    assert request.params["flavors"] == ["咸鲜"]
    assert request.params["methods"] == ["炒"]
    assert request.params["dish_types"] == ["热菜"]
    results = local.search_recipes("用鸡蛋推荐咸鲜热菜，不要香菜", constraints=filters)
    assert [row["name"] for row in results] == ["清炒鸡蛋"]


def test_no_spicy_filters_auxiliary_ingredients_even_when_flavor_is_mislabeled(tmp_path):
    _, local = _catalog(tmp_path)
    filters = RecipeConstraints(required_ingredients=["鸡蛋"], excluded_ingredients=["花生", "香菜"], avoid_spicy=True)
    local._recipes["辣炒鸡蛋"]["口味"] = "咸鲜"
    results = local.search_recipes("推荐鸡蛋菜", constraints=filters)
    assert [row["name"] for row in results] == ["清炒鸡蛋"]


def test_excluded_ingredient_is_not_treated_as_required(tmp_path):
    graph, _ = _catalog(tmp_path)
    filters = graph.extract_constraints("不要香菜，用鸡蛋推荐菜")
    assert filters.required_ingredients == ["鸡蛋"]
    assert filters.excluded_ingredients == ["香菜"]


def test_local_fallback_filters_before_limit(tmp_path):
    _, local = _catalog(tmp_path)
    filters = RecipeConstraints(required_ingredients=["鸡蛋"], excluded_ingredients=["香菜", "花生"], avoid_spicy=True)
    assert [row["name"] for row in local.search_recipes("推荐鸡蛋菜", limit=1, constraints=filters)] == ["清炒鸡蛋"]


def test_unknown_specific_query_does_not_return_unrelated_local_recipes(tmp_path):
    _, local = _catalog(tmp_path)
    assert local.search_recipes("如何制作不存在的菜", constraints=RecipeConstraints()) == []


def test_single_character_exclusions_do_not_match_inside_longer_names():
    filters = extract_constraints("用鸡蛋推荐一道家常菜，不要香菜，不吃葱，对虾过敏", ingredients=["香菜", "菜", "葱", "虾", "鸡蛋"],
                                  flavors=[], methods=[], dish_types=["家常菜"], dish_names=[], memories=[])
    assert set(filters.excluded_ingredients) == {"香菜", "葱", "虾"}
    assert "菜" not in filters.excluded_ingredients
