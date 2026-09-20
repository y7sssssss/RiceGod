from pathlib import Path

import pytest

from ricegod.evaluation.culture_retrieval import evaluate_culture_cases, load_culture_cases


ROOT = Path(__file__).resolve().parents[1]


def test_versioned_culture_suite_is_valid_and_in_requested_size_range():
    suite = load_culture_cases(ROOT / "data/evaluation/culture_retrieval_cases.json")

    assert suite["version"] == "1.0"
    assert 30 <= len(suite["cases"]) <= 50
    assert any(case["answerable"] for case in suite["cases"])
    assert any(not case["answerable"] for case in suite["cases"])


def test_culture_metrics_cover_recall_mrr_refusal_and_sources():
    suite = {
        "version": "test",
        "cases": [
            {"id": "hit", "query": "q1", "answerable": True,
             "expected_chunk_ids": ["data.txt_2"]},
            {"id": "miss", "query": "q2", "answerable": True,
             "expected_chunk_ids": ["data.txt_9"]},
            {"id": "reject", "query": "q3", "answerable": False,
             "expected_chunk_ids": []},
        ],
    }
    rows = {
        "q1": [
            {"id": "data.txt_1", "source": "data.txt", "chunk_index": 1, "score": 0.9},
            {"id": "data.txt_2", "source": "data.txt", "chunk_index": 2, "score": 0.8},
        ],
        "q2": [{"id": "data.txt_4", "source": "data.txt", "chunk_index": 4, "score": 0.7}],
        "q3": [],
    }

    report = evaluate_culture_cases(suite, lambda query, top_k: rows[query][:top_k], top_k=4)

    assert report["summary"]["recall_at_k"] == pytest.approx(0.5)
    assert report["summary"]["mrr"] == pytest.approx(0.25)
    assert report["summary"]["no_answer_accuracy"] == 1.0
    assert report["summary"]["source_coverage"] == pytest.approx(0.5)
    assert report["summary"]["passed_cases"] == 2


def test_culture_evaluation_does_not_count_search_errors_as_refusals():
    suite = {"version": "test", "cases": [
        {"id": "reject", "query": "q", "answerable": False, "expected_chunk_ids": []}
    ]}

    def broken_search(query, top_k):
        raise RuntimeError("unavailable")

    report = evaluate_culture_cases(suite, broken_search, top_k=4)

    assert report["summary"]["no_answer_accuracy"] == 0.0
    assert report["summary"]["passed_cases"] == 0
    assert report["cases"][0]["error_type"] == "RuntimeError"
