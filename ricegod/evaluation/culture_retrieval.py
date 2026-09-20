"""Metrics for the versioned food-culture retrieval suite."""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import Any, Callable


REQUIRED_SOURCE_FIELDS = ("source", "chunk_index")


def load_culture_cases(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload.get("version"), str) or not payload["version"]:
        raise ValueError("Culture suite requires a non-empty version.")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not 30 <= len(cases) <= 50:
        raise ValueError("Culture suite must contain 30 to 50 cases.")
    ids = set()
    for case in cases:
        if not isinstance(case, dict) or not case.get("id") or not case.get("query"):
            raise ValueError("Every culture case requires id and query.")
        if case["id"] in ids:
            raise ValueError(f"Duplicate culture case id: {case['id']}")
        ids.add(case["id"])
        expected = case.get("expected_chunk_ids")
        if not isinstance(expected, list):
            raise ValueError(f"Case {case['id']} requires expected_chunk_ids.")
        answerable = bool(case.get("answerable", True))
        if answerable != bool(expected):
            raise ValueError(f"Case {case['id']} answerable must match whether expected documents exist.")
    return payload


def evaluate_culture_cases(
    suite: dict[str, Any],
    search: Callable[[str, int], list[dict[str, Any]]],
    *,
    top_k: int,
) -> dict[str, Any]:
    results = []
    answerable_rr = []
    answerable_hits = []
    no_answer_hits = []
    source_hits = []

    for case in suite["cases"]:
        error_type = None
        try:
            rows = search(case["query"], top_k)
        except Exception as exc:  # The report retains failures instead of losing the full run.
            rows = []
            error_type = type(exc).__name__
        expected = set(case["expected_chunk_ids"])
        rank = next((index for index, row in enumerate(rows, start=1)
                     if row.get("id") in expected), None)
        answerable = bool(case.get("answerable", True))
        if answerable:
            hit = rank is not None
            answerable_hits.append(hit)
            answerable_rr.append(1.0 / rank if rank else 0.0)
            matching = next((row for row in rows if row.get("id") in expected), None)
            source_covered = bool(matching and all(matching.get(field) for field in REQUIRED_SOURCE_FIELDS))
            source_hits.append(source_covered)
            passed = hit and source_covered and error_type is None
        else:
            hit = not rows and error_type is None
            no_answer_hits.append(hit)
            source_covered = None
            passed = hit and error_type is None
        results.append({
            "id": case["id"],
            "category": case.get("category", ""),
            "query": case["query"],
            "answerable": answerable,
            "expected_chunk_ids": case["expected_chunk_ids"],
            "returned_chunk_ids": [row.get("id") for row in rows],
            "returned_scores": [row.get("score") for row in rows],
            "returned_vector_scores": [row.get("vector_score") for row in rows],
            "rank": rank,
            "source_covered": source_covered,
            "passed": passed,
            "error_type": error_type,
        })

    ratio = lambda values: sum(values) / len(values) if values else None
    summary = {
        "cases": len(results),
        "answerable_cases": len(answerable_hits),
        "unanswerable_cases": len(no_answer_hits),
        "recall_at_k": ratio(answerable_hits),
        "mrr": mean(answerable_rr) if answerable_rr else None,
        "no_answer_accuracy": ratio(no_answer_hits),
        "source_coverage": ratio(source_hits),
        "passed_cases": sum(item["passed"] for item in results),
    }
    return {"suite_version": suite["version"], "top_k": top_k, "summary": summary, "cases": results,
            "passed": all(item["passed"] for item in results)}


def write_culture_report(report: dict[str, Any], output: str | Path) -> None:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = report["summary"]

    def percent(value):
        return "无适用样本" if value is None else f"{value:.1%}"

    lines = [
        "# 文化检索固定评测",
        "",
        f"固定集版本：`{report['suite_version']}`；Top-K：{report['top_k']}。",
        "",
        "该评测衡量检索文档命中、拒答和来源字段，不判断历史陈述本身的真实性。",
        "",
        "| 指标 | 结果 |",
        "| --- | ---: |",
        f"| Recall@K | {percent(summary['recall_at_k'])} |",
        f"| MRR | {summary['mrr']:.3f} |" if summary["mrr"] is not None else "| MRR | 无适用样本 |",
        f"| 无答案识别率 | {percent(summary['no_answer_accuracy'])} |",
        f"| 来源覆盖率 | {percent(summary['source_coverage'])} |",
        f"| 通过案例 | {summary['passed_cases']}/{summary['cases']} |",
        "",
        "## 失败案例",
        "",
    ]
    failed = [case for case in report["cases"] if not case["passed"]]
    if failed:
        for case in failed:
            lines.append(
                f"- `{case['id']}`：期望 {case['expected_chunk_ids']}，"
                f"实际 {case['returned_chunk_ids']}，错误 {case['error_type'] or '无'}"
            )
    else:
        lines.append("本轮没有失败案例。")
    path.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
