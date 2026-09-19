from ricegod.domain.graph_coverage import build_graph_coverage_report
from scripts.import_recipes_to_neo4j import build_import_rows


def test_graph_coverage_reports_effective_aliases_and_integrity():
    rows = build_import_rows({
        "番茄炒蛋": {
            "主食材": [["番茄", "1个"], ["蛋液", "2个"]],
            "辅料": [["盐", "适量"]],
            "口味": "清淡",
            "工艺": "炒",
            "类型": "热菜",
            "耗时": "十分钟",
            "做法": "炒熟",
        }
    })

    report = build_graph_coverage_report(rows)

    assert report["entities"]["dishes"] == 1
    assert report["entities"]["canonical_ingredients"] == 3
    assert report["entities"]["ingredient_aliases"] == 2
    assert report["quality"]["self_aliases"] == 0
    assert report["quality"]["dangling_alias_targets"] == 0
    assert report["coverage"]["dish_fields"]["instructions"]["rate"] == 1.0
    assert report["coverage"]["substitutions"]["source_complete"] == 4
