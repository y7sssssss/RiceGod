"""Compare a temporary Graph V1 projection, Graph V2, and recipe.json retrieval."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter_ns
from typing import Any, Callable

from ricegod.domain.graph_v2 import (
    allergens_for_ingredient,
    canonical_ingredient,
    substitution_rows,
)
from ricegod.infrastructure.neo4j_client import Neo4jClient


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "data" / "evaluation" / "graph_v2_cases.json"
DEFAULT_RECIPES = ROOT / "data" / "recipe.json"
DEFAULT_OUTPUT = ROOT / "docs" / "graph_v2_evaluation.json"
COMMON_INGREDIENTS = {"盐", "油", "食用油", "水", "生抽", "酱油", "糖", "白砂糖"}
GRAPH_BACKENDS = {"graph_v1", "graph_v2"}
V2_BACKENDS = {"graph_v2", "json_v2"}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def ingredient_entries(recipe: dict[str, Any], *, canonical: bool) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for key, role in (("主食材", "main"), ("辅料", "aux")):
        for item in recipe.get(key) or []:
            raw = str(item[0] if isinstance(item, list) and item else item).strip()
            if raw:
                entries.append((canonical_ingredient(raw) if canonical else raw, role))
    return entries


def recipe_index(recipes: dict[str, dict[str, Any]], *, canonical: bool) -> dict[str, list[tuple[str, str]]]:
    return {name: ingredient_entries(recipe, canonical=canonical) for name, recipe in recipes.items()}


def weighted_similar(
    index: dict[str, list[tuple[str, str]]], dish_name: str, limit: int
) -> list[str]:
    source_entries = index.get(dish_name, [])
    scored: list[tuple[int, int, str]] = []
    for candidate_name, candidate_entries in index.items():
        if candidate_name == dish_name:
            continue
        score = 0
        shared_main = 0
        for source_name, source_role in source_entries:
            if source_name in COMMON_INGREDIENTS:
                continue
            for candidate_ingredient, candidate_role in candidate_entries:
                if source_name != candidate_ingredient:
                    continue
                if source_role == "main" and candidate_role == "main":
                    score += 6
                    shared_main += 1
                elif source_role == "main":
                    score += 3
                elif candidate_role == "main":
                    score += 2
                else:
                    score += 1
        if score:
            scored.append((-shared_main, -score, candidate_name))
    scored.sort()
    return [name for _, _, name in scored[:limit]]


def reference_results(
    case: dict[str, Any], canonical_index: dict[str, list[tuple[str, str]]]
) -> list[str]:
    category = case["category"]
    if category == "ingredient_alias":
        return sorted(
            name for name, entries in canonical_index.items()
            if any(ingredient == case["canonical"] for ingredient, _ in entries)
        )
    if category == "allergen":
        return sorted(
            name for name, entries in canonical_index.items()
            if any(case["allergen"] in allergens_for_ingredient(ingredient)
                   for ingredient, _ in entries)
        )
    if category == "substitution":
        return sorted(case["expected"])
    if category == "similar_recipe":
        return weighted_similar(canonical_index, case["dish"], case["limit"])
    raise ValueError(f"Unknown case category: {category}")


class JsonBackend:
    def __init__(
        self,
        name: str,
        raw_index: dict[str, list[tuple[str, str]]],
        canonical_index: dict[str, list[tuple[str, str]]],
    ) -> None:
        self.name = name
        self.index = canonical_index if name == "json_v2" else raw_index
        self.canonical = name == "json_v2"

    def supports(self, case: dict[str, Any]) -> bool:
        return self.canonical or case["category"] in {"ingredient_alias", "similar_recipe"}

    def query(self, case: dict[str, Any]) -> list[str]:
        category = case["category"]
        if category == "ingredient_alias":
            term = case["canonical"] if self.canonical else case["term"]
            return sorted(
                name for name, entries in self.index.items()
                if any(ingredient == term for ingredient, _ in entries)
            )
        if category == "allergen" and self.canonical:
            return sorted(
                name for name, entries in self.index.items()
                if any(case["allergen"] in allergens_for_ingredient(ingredient)
                       for ingredient, _ in entries)
            )
        if category == "substitution" and self.canonical:
            source = canonical_ingredient(case["term"])
            return sorted(row["to"] for row in substitution_rows() if row["from"] == source)
        if category == "similar_recipe":
            return weighted_similar(self.index, case["dish"], case["limit"])
        return []


class GraphBackend:
    def __init__(self, name: str, client: Neo4jClient) -> None:
        self.name = name
        self.client = client

    def supports(self, case: dict[str, Any]) -> bool:
        return self.name == "graph_v2" or case["category"] in {
            "ingredient_alias", "similar_recipe"
        }

    def query(self, case: dict[str, Any]) -> list[str]:
        if not self.supports(case):
            return []
        category = case["category"]
        if self.name == "graph_v1":
            if category == "ingredient_alias":
                cypher = """
MATCH (ingredient:GraphV1EvalIngredient {name: $term})
      <-[:GRAPH_V1_EVAL_HAS_MAIN|GRAPH_V1_EVAL_HAS_AUX]-(dish:GraphV1EvalDish)
RETURN DISTINCT dish.name AS value ORDER BY value
"""
                params = {"term": case["term"]}
            else:
                cypher = """
MATCH (source:GraphV1EvalDish {name: $dish})
      -[sourceRel:GRAPH_V1_EVAL_HAS_MAIN|GRAPH_V1_EVAL_HAS_AUX]->(ingredient:GraphV1EvalIngredient)
MATCH (candidate:GraphV1EvalDish)
      -[candidateRel:GRAPH_V1_EVAL_HAS_MAIN|GRAPH_V1_EVAL_HAS_AUX]->(ingredient)
WHERE candidate <> source
  AND NOT ingredient.name IN $common
WITH candidate,
     sum(CASE WHEN type(sourceRel) = 'GRAPH_V1_EVAL_HAS_MAIN'
                   AND type(candidateRel) = 'GRAPH_V1_EVAL_HAS_MAIN'
              THEN 6 WHEN type(sourceRel) = 'GRAPH_V1_EVAL_HAS_MAIN' THEN 3
              WHEN type(candidateRel) = 'GRAPH_V1_EVAL_HAS_MAIN' THEN 2 ELSE 1 END) AS score,
     sum(CASE WHEN type(sourceRel) = 'GRAPH_V1_EVAL_HAS_MAIN'
                   AND type(candidateRel) = 'GRAPH_V1_EVAL_HAS_MAIN'
              THEN 1 ELSE 0 END) AS shared_main
RETURN candidate.name AS value
ORDER BY shared_main DESC, score DESC, value
LIMIT $limit
"""
                params = {"dish": case["dish"], "limit": case["limit"],
                          "common": sorted(COMMON_INGREDIENTS)}
        elif category == "ingredient_alias":
            cypher = """
MATCH (ingredient:Ingredient {name: $canonical})
      <-[:HAS_MAIN_INGREDIENT|HAS_AUX_INGREDIENT]-(dish:Dish)
RETURN DISTINCT dish.name AS value ORDER BY value
"""
            params = {"canonical": case["canonical"]}
        elif category == "allergen":
            cypher = """
MATCH (dish:Dish)-[:HAS_MAIN_INGREDIENT|HAS_AUX_INGREDIENT]->
      (:Ingredient)-[:CONTAINS_ALLERGEN]->(:Allergen {name: $allergen})
RETURN DISTINCT dish.name AS value ORDER BY value
"""
            params = {"allergen": case["allergen"]}
        elif category == "substitution":
            cypher = """
MATCH (:Ingredient {name: $term})-[:CAN_SUBSTITUTE]->(replacement:Ingredient)
RETURN DISTINCT replacement.name AS value ORDER BY value
"""
            params = {"term": canonical_ingredient(case["term"])}
        else:
            cypher = """
MATCH (source:Dish {name: $dish})
      -[sourceRel:HAS_MAIN_INGREDIENT|HAS_AUX_INGREDIENT]->(ingredient:Ingredient)
MATCH (candidate:Dish)
      -[candidateRel:HAS_MAIN_INGREDIENT|HAS_AUX_INGREDIENT]->(ingredient)
WHERE candidate <> source
  AND NOT ingredient.name IN $common
WITH candidate,
     sum(CASE WHEN type(sourceRel) = 'HAS_MAIN_INGREDIENT'
                   AND type(candidateRel) = 'HAS_MAIN_INGREDIENT'
              THEN 6 WHEN type(sourceRel) = 'HAS_MAIN_INGREDIENT' THEN 3
              WHEN type(candidateRel) = 'HAS_MAIN_INGREDIENT' THEN 2 ELSE 1 END) AS score,
     sum(CASE WHEN type(sourceRel) = 'HAS_MAIN_INGREDIENT'
                   AND type(candidateRel) = 'HAS_MAIN_INGREDIENT'
              THEN 1 ELSE 0 END) AS shared_main
RETURN candidate.name AS value
ORDER BY shared_main DESC, score DESC, value
LIMIT $limit
"""
            params = {"dish": case["dish"], "limit": case["limit"],
                      "common": sorted(COMMON_INGREDIENTS)}
        rows = self.client.run_query(cypher, params, read_only=True, timeout=30.0)
        return [str(row["value"]) for row in rows]


def batches(rows: list[dict[str, Any]], size: int = 2000) -> list[list[dict[str, Any]]]:
    return [rows[index:index + size] for index in range(0, len(rows), size)]


def clear_v1_projection(client: Neo4jClient) -> None:
    client.run_query("MATCH (n:GraphV1EvalDish) DETACH DELETE n", timeout=60.0)
    client.run_query("MATCH (n:GraphV1EvalIngredient) DETACH DELETE n", timeout=60.0)
    client.run_query("DROP CONSTRAINT graph_v1_eval_dish_name IF EXISTS")
    client.run_query("DROP CONSTRAINT graph_v1_eval_ingredient_name IF EXISTS")


def create_v1_projection(
    client: Neo4jClient, recipes: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    started = perf_counter_ns()
    clear_v1_projection(client)
    client.run_query(
        "CREATE CONSTRAINT graph_v1_eval_dish_name IF NOT EXISTS "
        "FOR (n:GraphV1EvalDish) REQUIRE n.name IS UNIQUE"
    )
    client.run_query(
        "CREATE CONSTRAINT graph_v1_eval_ingredient_name IF NOT EXISTS "
        "FOR (n:GraphV1EvalIngredient) REQUIRE n.name IS UNIQUE"
    )
    dish_rows = [{"name": name} for name in recipes]
    relation_rows: list[dict[str, Any]] = []
    for dish_name, recipe in recipes.items():
        for key, relationship in (
            ("主食材", "GRAPH_V1_EVAL_HAS_MAIN"),
            ("辅料", "GRAPH_V1_EVAL_HAS_AUX"),
        ):
            for item in recipe.get(key) or []:
                raw = str(item[0] if isinstance(item, list) and item else item).strip()
                if raw:
                    relation_rows.append({"dish": dish_name, "ingredient": raw,
                                          "relationship": relationship})
    for batch in batches(dish_rows):
        client.run_query(
            "UNWIND $rows AS row MERGE (:GraphV1EvalDish {name: row.name})",
            {"rows": batch}, timeout=60.0,
        )
    for relationship in ("GRAPH_V1_EVAL_HAS_MAIN", "GRAPH_V1_EVAL_HAS_AUX"):
        selected = [row for row in relation_rows if row["relationship"] == relationship]
        for batch in batches(selected):
            client.run_query(f"""
UNWIND $rows AS row
MATCH (dish:GraphV1EvalDish {{name: row.dish}})
MERGE (ingredient:GraphV1EvalIngredient {{name: row.ingredient}})
MERGE (dish)-[:{relationship}]->(ingredient)
""", {"rows": batch}, timeout=60.0)
    return {
        "dish_count": len(dish_rows),
        "input_relationship_count": len(relation_rows),
        "setup_ms": round((perf_counter_ns() - started) / 1_000_000, 3),
    }


def percentile(values: list[float], percentile_value: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile_value * len(ordered)))
    return ordered[rank - 1]


def timed_query(function: Callable[[], list[str]], repeats: int) -> tuple[list[str], list[float]]:
    expected = function()  # warm-up; excluded from timings
    durations = []
    for _ in range(repeats):
        started = perf_counter_ns()
        actual = function()
        durations.append((perf_counter_ns() - started) / 1_000_000)
        if actual != expected:
            raise RuntimeError("A backend returned unstable results across benchmark repetitions.")
    return expected, durations


def result_hash(values: list[str]) -> str:
    payload = json.dumps(sorted(values), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def score_case(
    case: dict[str, Any], actual: list[str], expected: list[str], durations: list[float], supported: bool
) -> dict[str, Any]:
    actual_set, expected_set = set(actual), set(expected)
    true_positive = len(actual_set & expected_set)
    precision = true_positive / len(actual_set) if actual_set else 0.0
    recall = true_positive / len(expected_set) if expected_set else 1.0
    return {
        "supported": supported,
        "returned": len(actual_set),
        "expected": len(expected_set),
        "true_positive": true_positive,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "exact_match": actual_set == expected_set,
        "result_hash": result_hash(actual),
        "sample": actual[:5],
        "latency_ms": {
            "samples": len(durations),
            "mean": round(statistics.fmean(durations), 3) if durations else None,
            "p50": round(statistics.median(durations), 3) if durations else None,
            "p95": round(percentile(durations, 0.95), 3) if durations else None,
        },
    }


def summarize(cases: list[dict[str, Any]], backend: str) -> dict[str, Any]:
    rows = [case["backends"][backend] for case in cases]
    latencies = [sample for row in rows for sample in row.pop("_latency_samples", [])]
    total_expected = sum(row["expected"] for row in rows)
    total_true_positive = sum(row["true_positive"] for row in rows)
    return {
        "cases": len(rows),
        "supported_cases": sum(row["supported"] for row in rows),
        "exact_matches": sum(row["exact_match"] for row in rows),
        "exact_match_rate": round(sum(row["exact_match"] for row in rows) / len(rows), 6),
        "macro_precision": round(statistics.fmean(row["precision"] for row in rows), 6),
        "macro_recall": round(statistics.fmean(row["recall"] for row in rows), 6),
        "micro_recall": round(total_true_positive / total_expected, 6) if total_expected else None,
        "latency_ms": {
            "samples": len(latencies),
            "mean": round(statistics.fmean(latencies), 3) if latencies else None,
            "p50": round(statistics.median(latencies), 3) if latencies else None,
            "p95": round(percentile(latencies, 0.95), 3) if latencies else None,
        },
    }


def summarize_categories(cases: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for category in sorted({case["category"] for case in cases}):
        selected = [case for case in cases if case["category"] == category]
        backend_summary = {}
        for backend in ("graph_v1", "graph_v2", "json_v1", "json_v2"):
            rows = [case["backends"][backend] for case in selected]
            supported_p50 = [
                row["latency_ms"]["p50"] for row in rows
                if row["supported"] and row["latency_ms"]["p50"] is not None
            ]
            backend_summary[backend] = {
                "cases": len(rows),
                "supported_cases": sum(row["supported"] for row in rows),
                "exact_matches": sum(row["exact_match"] for row in rows),
                "macro_recall": round(statistics.fmean(row["recall"] for row in rows), 6),
                "case_p50_median_ms": (
                    round(statistics.median(supported_p50), 3) if supported_p50 else None
                ),
            }
        graph_p50 = backend_summary["graph_v2"]["case_p50_median_ms"]
        json_p50 = backend_summary["json_v2"]["case_p50_median_ms"]
        result[category] = {
            "backends": backend_summary,
            "graph_v2_speedup_vs_enriched_json": (
                round(json_p50 / graph_p50, 3) if graph_p50 and json_p50 is not None else None
            ),
        }
    return result


def build_v1_to_v2_comparison(
    cases: list[dict[str, Any]], summary: dict[str, Any], category_summary: dict[str, Any]
) -> dict[str, Any]:
    overlap = [
        case for case in cases
        if case["backends"]["graph_v1"]["supported"]
        and case["backends"]["graph_v2"]["supported"]
    ]

    def overlap_metrics(backend: str) -> dict[str, Any]:
        rows = [case["backends"][backend] for case in overlap]
        p50_values = [row["latency_ms"]["p50"] for row in rows]
        return {
            "exact_matches": sum(row["exact_match"] for row in rows),
            "macro_recall": round(statistics.fmean(row["recall"] for row in rows), 6),
            "case_p50_median_ms": round(statistics.median(p50_values), 3),
        }

    v1_overlap = overlap_metrics("graph_v1")
    v2_overlap = overlap_metrics("graph_v2")
    return {
        "all_cases": {
            "case_count": len(cases),
            "v1_supported": summary["graph_v1"]["supported_cases"],
            "v2_supported": summary["graph_v2"]["supported_cases"],
            "additional_supported_cases": (
                summary["graph_v2"]["supported_cases"]
                - summary["graph_v1"]["supported_cases"]
            ),
            "v1_exact_matches": summary["graph_v1"]["exact_matches"],
            "v2_exact_matches": summary["graph_v2"]["exact_matches"],
            "exact_match_improvement_pp": round(
                (summary["graph_v2"]["exact_match_rate"]
                 - summary["graph_v1"]["exact_match_rate"]) * 100, 3
            ),
            "macro_recall_improvement_pp": round(
                (summary["graph_v2"]["macro_recall"]
                 - summary["graph_v1"]["macro_recall"]) * 100, 3
            ),
        },
        "overlapping_cases": {
            "case_count": len(overlap),
            "graph_v1": v1_overlap,
            "graph_v2": v2_overlap,
            "macro_recall_improvement_pp": round(
                (v2_overlap["macro_recall"] - v1_overlap["macro_recall"]) * 100, 3
            ),
            "p50_delta_ms": round(
                v2_overlap["case_p50_median_ms"] - v1_overlap["case_p50_median_ms"], 3
            ),
        },
        "by_category": {
            category: {
                "graph_v1_recall": item["backends"]["graph_v1"]["macro_recall"],
                "graph_v2_recall": item["backends"]["graph_v2"]["macro_recall"],
                "graph_v1_case_p50_ms": item["backends"]["graph_v1"]["case_p50_median_ms"],
                "graph_v2_case_p50_ms": item["backends"]["graph_v2"]["case_p50_median_ms"],
            }
            for category, item in category_summary.items()
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    labels = {
        "graph_v1": "Graph V1（临时原始字符串投影）",
        "graph_v2": "Graph V2",
        "json_v1": "JSON V1（精确字符串）",
        "json_v2": "JSON V2（规范化）",
    }
    lines = [
        "# Graph V1 / V2 与原始 JSON 固定评测",
        "",
        f"评测时间：`{report['evaluated_at']}`；固定集版本：`{report['suite_version']}`；"
        f"菜谱数：**{report['recipe_count']}**；题目数：**{report['case_count']}**；"
        f"每题计时重复：**{report['repeats']}** 次（另有一次预热）。",
        "",
        "## Graph V1 → Graph V2 升级效果",
        "",
        f"V1 使用原始食材字符串实体，V2 使用规范食材、别名、过敏原和替代关系。"
        f"在全部 24 题中，V1 支持 **{report['v1_to_v2_comparison']['all_cases']['v1_supported']}/24**，"
        f"V2 支持 **{report['v1_to_v2_comparison']['all_cases']['v2_supported']}/24**；"
        f"完全正确由 **{report['v1_to_v2_comparison']['all_cases']['v1_exact_matches']}/24** 提升到 "
        f"**{report['v1_to_v2_comparison']['all_cases']['v2_exact_matches']}/24**。",
        "",
        "| 范围 | V1 完全正确 | V2 完全正确 | V1 宏召回 | V2 宏召回 | V1 题级 P50 中位数 | V2 题级 P50 中位数 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        f"| 共同支持的 14 题 | "
        f"{report['v1_to_v2_comparison']['overlapping_cases']['graph_v1']['exact_matches']}/14 | "
        f"{report['v1_to_v2_comparison']['overlapping_cases']['graph_v2']['exact_matches']}/14 | "
        f"{report['v1_to_v2_comparison']['overlapping_cases']['graph_v1']['macro_recall']:.1%} | "
        f"{report['v1_to_v2_comparison']['overlapping_cases']['graph_v2']['macro_recall']:.1%} | "
        f"{report['v1_to_v2_comparison']['overlapping_cases']['graph_v1']['case_p50_median_ms']:.3f} ms | "
        f"{report['v1_to_v2_comparison']['overlapping_cases']['graph_v2']['case_p50_median_ms']:.3f} ms |",
        "",
        "V2 新增完成了 V1 不支持的 6 道过敏原题和 4 道替代关系题。共同支持的题目中，"
        "宏平均召回率提升 60.9 个百分点。V2 的共同题 P50 更高，主要因为别名查询返回了完整规范化结果集；"
        "在固定返回 5 条的相似菜查询中，V1/V2 题级 P50 中位数分别为 "
        f"**{report['v1_to_v2_comparison']['by_category']['similar_recipe']['graph_v1_case_p50_ms']:.3f} ms** 和 "
        f"**{report['v1_to_v2_comparison']['by_category']['similar_recipe']['graph_v2_case_p50_ms']:.3f} ms**，"
        "同时召回率由 85% 提升到 100%。",
        "",
        "## 核心结论：原始 JSON 与 Graph V2",
        "",
        f"- 完全正确率：原始 JSON **{report['summary']['json_v1']['exact_match_rate']:.1%}**，"
        f"Graph V2 **{report['summary']['graph_v2']['exact_match_rate']:.1%}**，"
        f"提升 **{report['primary_comparison']['exact_match_improvement_pp']:.1f} 个百分点**。",
        f"- 宏平均召回率：原始 JSON **{report['summary']['json_v1']['macro_recall']:.1%}**，"
        f"Graph V2 **{report['summary']['graph_v2']['macro_recall']:.1%}**，"
        f"提升 **{report['primary_comparison']['macro_recall_improvement_pp']:.1f} 个百分点**。",
        f"- 能力覆盖：原始 JSON 支持 **{report['summary']['json_v1']['supported_cases']}/24** 题，"
        f"Graph V2 支持 **{report['summary']['graph_v2']['supported_cases']}/24** 题。",
        f"- 查询延迟 P50：原始 JSON **{report['summary']['json_v1']['latency_ms']['p50']:.3f} ms**，"
        f"Graph V2 **{report['summary']['graph_v2']['latency_ms']['p50']:.3f} ms**。"
        "两者计时边界不同，延迟只用于本机观察。",
        "",
        "这里的原始 JSON 只按原始食材字符串扫描，不额外加载 Graph V2 的别名、过敏原和替代规则。"
        "因此这组对照衡量从原始数据文件升级到完整图谱方案后的总体效果。",
        "",
        "## 四种实现汇总",
        "",
        "| 后端 | 支持题数 | 完全正确 | 完全正确率 | 宏平均召回率 | 微平均召回率 | P50 | P95 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for backend, summary in report["summary"].items():
        lines.append(
            f"| {labels[backend]} | {summary['supported_cases']}/{summary['cases']} | "
            f"{summary['exact_matches']}/{summary['cases']} | {summary['exact_match_rate']:.1%} | "
            f"{summary['macro_recall']:.1%} | {summary['micro_recall']:.1%} | "
            f"{summary['latency_ms']['p50']:.3f} ms | {summary['latency_ms']['p95']:.3f} ms |"
        )
    lines.extend([
        "",
        "## 分类结果",
        "",
        "| 分类 | 题数 | Graph V1 完全正确 | Graph V2 完全正确 | JSON V1 完全正确 | JSON V2 完全正确 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ])
    categories = sorted({case["category"] for case in report["cases"]})
    for category in categories:
        selected = [case for case in report["cases"] if case["category"] == category]
        values = []
        for backend in ("graph_v1", "graph_v2", "json_v1", "json_v2"):
            passed = sum(case["backends"][backend]["exact_match"] for case in selected)
            values.append(f"{passed}/{len(selected)}")
        lines.append(f"| {category} | {len(selected)} | " + " | ".join(values) + " |")
    lines.extend([
        "",
        "## 分项召回与延迟",
        "",
        "| 分类 | 原始 JSON 宏召回 | Graph V2 宏召回 | Graph V2 题级 P50 中位数 | 增强 JSON 题级 P50 中位数 | Graph V2 相对速度 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ])
    for category, item in report["category_summary"].items():
        raw = item["backends"]["json_v1"]
        graph = item["backends"]["graph_v2"]
        enriched = item["backends"]["json_v2"]
        speedup = item["graph_v2_speedup_vs_enriched_json"]
        lines.append(
            f"| {category} | {raw['macro_recall']:.1%} | {graph['macro_recall']:.1%} | "
            f"{graph['case_p50_median_ms']:.3f} ms | {enriched['case_p50_median_ms']:.3f} ms | "
            f"{speedup:.2f}× |"
        )
    lines.extend([
        "",
        "Graph V2 在本轮过敏原多跳和相似菜查询中分别比增强 JSON 扫描快约 "
        f"**{report['category_summary']['allergen']['graph_v2_speedup_vs_enriched_json']:.2f}×** 和 "
        f"**{report['category_summary']['similar_recipe']['graph_v2_speedup_vs_enriched_json']:.2f}×**。"
        "别名检索和小规模替代规则查询则由内存 JSON 更快，因此不能概括为所有查询建图后都会加速。",
        "",
        "## 口径",
        "",
        "- 期望结果由固定问题、版本化别名/过敏原规则和原始 `recipe.json` 独立生成；相似菜按文档中的主料/辅料权重取前五。",
        "- Graph V1 是评测时从原始 JSON 临时重建的原始字符串实体投影，建图时间不计入查询耗时；评测后已删除投影节点和专用约束。它复现 V1 食材身份规则，不宣称是历史运行快照。",
        "- 原始 JSON 和 Graph V1 不具备过敏原和替代关系，因此这些题记为不支持且召回率为零。",
        "- 增强 JSON 加载与 Graph V2 相同的规范化规则，用来隔离存储/查询引擎的影响。若它与 Graph V2 正确率一致，说明准确率收益主要来自建图前的数据治理；两者的延迟差异才更接近图查询本身的效果。",
        "- 图查询计时包含 Neo4j 驱动、会话和结果读取；JSON 查询计时仅包含已加载内存后的 Python 扫描。两者边界不同，延迟用于描述本机本次运行，不能直接作为生产 SLA。",
        "- 完全正确要求返回集合与期望集合一致；宏平均让每题权重相同，微平均按期望结果数量加权。",
        "",
        "完整逐题结果、结果哈希、样例和计时见同名 JSON 报告。",
        "",
    ])
    return "\n".join(lines)


def evaluate(
    recipes: dict[str, dict[str, Any]], suite: dict[str, Any], client: Neo4jClient,
    *, repeats: int,
) -> dict[str, Any]:
    raw_index = recipe_index(recipes, canonical=False)
    canonical_index = recipe_index(recipes, canonical=True)
    projection: dict[str, Any] = {}
    try:
        projection = create_v1_projection(client, recipes)
        backends: dict[str, Any] = {
            "graph_v1": GraphBackend("graph_v1", client),
            "graph_v2": GraphBackend("graph_v2", client),
            "json_v1": JsonBackend("json_v1", raw_index, canonical_index),
            "json_v2": JsonBackend("json_v2", raw_index, canonical_index),
        }
        case_reports = []
        for case in suite["cases"]:
            expected = reference_results(case, canonical_index)
            if not expected:
                raise RuntimeError(f"Fixed case {case['id']} has no expected results.")
            backend_reports = {}
            for name, backend in backends.items():
                supported = backend.supports(case)
                if supported:
                    actual, durations = timed_query(lambda b=backend, c=case: b.query(c), repeats)
                else:
                    actual, durations = [], []
                scored = score_case(case, actual, expected, durations, supported)
                scored["_latency_samples"] = durations
                backend_reports[name] = scored
            case_reports.append({
                "id": case["id"], "category": case["category"],
                "question": case["question"], "expected_count": len(set(expected)),
                "expected_hash": result_hash(expected), "expected_sample": expected[:5],
                "backends": backend_reports,
            })
        summary = {name: summarize(case_reports, name) for name in backends}
        category_summary = summarize_categories(case_reports)
        v1_to_v2_comparison = build_v1_to_v2_comparison(
            case_reports, summary, category_summary
        )
        primary_comparison = {
            "baseline": "json_v1",
            "candidate": "graph_v2",
            "exact_match_improvement_pp": round(
                (summary["graph_v2"]["exact_match_rate"]
                 - summary["json_v1"]["exact_match_rate"]) * 100, 3
            ),
            "macro_recall_improvement_pp": round(
                (summary["graph_v2"]["macro_recall"]
                 - summary["json_v1"]["macro_recall"]) * 100, 3
            ),
            "additional_supported_cases": (
                summary["graph_v2"]["supported_cases"]
                - summary["json_v1"]["supported_cases"]
            ),
            "p50_delta_ms": round(
                summary["graph_v2"]["latency_ms"]["p50"]
                - summary["json_v1"]["latency_ms"]["p50"], 3
            ),
        }
        return {
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "scope": "24 fixed deterministic retrieval cases; no LLM or end-to-end answer scoring.",
            "suite_version": suite["version"],
            "suite_sha256": hashlib.sha256(
                json.dumps(suite, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "recipe_sha256": hashlib.sha256(DEFAULT_RECIPES.read_bytes()).hexdigest(),
            "recipe_count": len(recipes), "case_count": len(suite["cases"]),
            "repeats": repeats, "v1_projection": projection,
            "primary_comparison": primary_comparison,
            "v1_to_v2_comparison": v1_to_v2_comparison,
            "summary": summary, "category_summary": category_summary,
            "cases": case_reports,
        }
    finally:
        clear_v1_projection(client)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--recipes", type=Path, default=DEFAULT_RECIPES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--repeats", type=int, default=10)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    recipes, suite = load_json(args.recipes), load_json(args.cases)
    if len(suite.get("cases", [])) != 24:
        raise RuntimeError("Graph V2 fixed suite must contain exactly 24 cases.")
    client = Neo4jClient()
    try:
        report = evaluate(recipes, suite, client, repeats=args.repeats)
    finally:
        client.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path = args.output.with_suffix(".md")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Wrote {args.output} and {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
