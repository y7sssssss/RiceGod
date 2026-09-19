from scripts.import_recipes_to_neo4j import build_import_rows


def test_build_import_rows_splits_recipe_graph_rows():
    recipes = {"香肠炒菜干": {
        "主食材": [["香肠", "2根"], ["菜干", "200g"]], "辅料": [["盐", "适量"]],
        "耗时": "十分钟", "口味": "酱香", "工艺": "炒", "做法": "1. 准备食材。2. 炒熟。", "类型": "热菜"
    }}
    rows = build_import_rows(recipes)
    assert rows["dishes"] == [{"name": "香肠炒菜干", "cook_time": "十分钟", "instructions": "1. 准备食材。2. 炒熟。"}]
    assert rows["main_ingredients"] == [
        {"dish_name": "香肠炒菜干", "ingredient_name": "香肠", "raw_name": "香肠", "amount_text": "2根"},
        {"dish_name": "香肠炒菜干", "ingredient_name": "菜干", "raw_name": "菜干", "amount_text": "200g"},
    ]
    assert rows["aux_ingredients"] == [
        {"dish_name": "香肠炒菜干", "ingredient_name": "盐", "raw_name": "盐", "amount_text": "适量"}
    ]
    assert rows["flavors"] == [{"dish_name": "香肠炒菜干", "value": "酱香"}]
    assert rows["methods"] == [{"dish_name": "香肠炒菜干", "value": "炒"}]
    assert rows["types"] == [{"dish_name": "香肠炒菜干", "value": "热菜"}]
    assert rows["aliases"] == []


def test_graph_v2_rows_normalize_aliases_and_attach_allergen_provenance():
    rows = build_import_rows({"番茄蛋汤": {
        "主食材": [["番茄", "1个"], ["蛋液", "2个"]], "辅料": [],
        "口味": "清淡", "工艺": "煮", "类型": "汤", "耗时": "十分钟", "做法": "煮熟"
    }})
    assert [row["ingredient_name"] for row in rows["main_ingredients"]] == ["西红柿", "鸡蛋"]
    assert {tuple(row.values()) for row in rows["aliases"]} >= {("番茄", "西红柿"), ("蛋液", "鸡蛋")}
    assert all(row["name"] != row["canonical_name"] for row in rows["aliases"])
    assert {row["category"] for row in rows["ingredient_categories"]} >= {"茄果类蔬菜", "蛋类"}
    assert {row["allergen"] for row in rows["ingredient_allergens"]} == {"蛋类"}
    assert all(row["source_id"] == "fda-major-allergens" for row in rows["ingredient_allergens"])
