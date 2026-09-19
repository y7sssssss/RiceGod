import json
from pathlib import Path

from scripts.evaluate_graph_v2 import (
    build_v1_to_v2_comparison,
    JsonBackend,
    ingredient_entries,
    reference_results,
    result_hash,
    score_case,
    summarize_categories,
    weighted_similar,
)


ROOT = Path(__file__).resolve().parents[1]


def test_fixed_graph_v2_suite_has_24_unique_cases():
    suite = json.loads((ROOT / "data/evaluation/graph_v2_cases.json").read_text(encoding="utf-8"))
    cases = suite["cases"]
    assert len(cases) == 24
    assert len({case["id"] for case in cases}) == 24
    assert {case["category"] for case in cases} == {
        "ingredient_alias", "allergen", "substitution", "similar_recipe"
    }


def test_json_v2_alias_reference_and_v1_exact_behavior():
    recipes = {
        "番茄菜": {"主食材": [["番茄", "1个"]]},
        "西红柿菜": {"主食材": [["西红柿", "1个"]]},
        "土豆菜": {"主食材": [["土豆", "1个"]]},
    }
    raw = {name: ingredient_entries(row, canonical=False) for name, row in recipes.items()}
    canonical = {name: ingredient_entries(row, canonical=True) for name, row in recipes.items()}
    case = {"category": "ingredient_alias", "term": "番茄", "canonical": "西红柿"}
    assert reference_results(case, canonical) == ["番茄菜", "西红柿菜"]
    assert JsonBackend("json_v1", raw, canonical).query(case) == ["番茄菜"]
    assert JsonBackend("json_v2", raw, canonical).query(case) == ["番茄菜", "西红柿菜"]


def test_weighted_similarity_prefers_shared_main_ingredient():
    index = {
        "来源": [("鸡蛋", "main"), ("葱", "aux")],
        "主料相同": [("鸡蛋", "main")],
        "辅料相同": [("鸡蛋", "aux")],
        "只有葱": [("葱", "aux")],
    }
    assert weighted_similar(index, "来源", 3) == ["主料相同", "辅料相同", "只有葱"]


def test_case_scoring_reports_exact_match_and_stable_hash():
    case = {"category": "substitution"}
    score = score_case(case, ["冰糖"], ["冰糖"], [1.0, 2.0], True)
    assert score["exact_match"] is True
    assert score["precision"] == 1.0
    assert score["recall"] == 1.0
    assert score["result_hash"] == result_hash(["冰糖"])


def test_category_summary_compares_graph_with_enriched_json():
    def row(p50):
        return {
            "supported": True, "exact_match": True, "recall": 1.0,
            "latency_ms": {"p50": p50},
        }

    cases = [{"category": "allergen", "backends": {
        "graph_v1": {**row(None), "supported": False, "exact_match": False, "recall": 0.0},
        "graph_v2": row(10.0),
        "json_v1": {**row(None), "supported": False, "exact_match": False, "recall": 0.0},
        "json_v2": row(40.0),
    }}]
    summary = summarize_categories(cases)["allergen"]
    assert summary["graph_v2_speedup_vs_enriched_json"] == 4.0


def test_v1_to_v2_comparison_uses_only_shared_cases_for_latency():
    def backend(supported, exact, recall, p50):
        return {
            "supported": supported, "exact_match": exact, "recall": recall,
            "latency_ms": {"p50": p50},
        }

    cases = [
        {"category": "ingredient_alias", "backends": {
            "graph_v1": backend(True, False, 0.5, 2.0),
            "graph_v2": backend(True, True, 1.0, 3.0),
            "json_v1": backend(True, False, 0.5, 1.0),
            "json_v2": backend(True, True, 1.0, 1.5),
        }},
        {"category": "allergen", "backends": {
            "graph_v1": backend(False, False, 0.0, None),
            "graph_v2": backend(True, True, 1.0, 4.0),
            "json_v1": backend(False, False, 0.0, None),
            "json_v2": backend(True, True, 1.0, 8.0),
        }},
    ]
    category_summary = summarize_categories(cases)
    summary = {
        "graph_v1": {"supported_cases": 1, "exact_matches": 0,
                     "exact_match_rate": 0.0, "macro_recall": 0.25},
        "graph_v2": {"supported_cases": 2, "exact_matches": 2,
                     "exact_match_rate": 1.0, "macro_recall": 1.0},
    }
    comparison = build_v1_to_v2_comparison(cases, summary, category_summary)
    assert comparison["overlapping_cases"]["case_count"] == 1
    assert comparison["overlapping_cases"]["p50_delta_ms"] == 1.0
    assert comparison["all_cases"]["additional_supported_cases"] == 1
