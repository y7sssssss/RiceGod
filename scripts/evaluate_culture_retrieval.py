"""Evaluate live Milvus culture retrieval with versioned chunk-level expectations."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from ricegod.config import settings
from ricegod.evaluation.culture_retrieval import (
    evaluate_culture_cases,
    load_culture_cases,
    write_culture_report,
)
from ricegod.infrastructure.food_knowledge_repository import FoodKnowledgeRepository


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=ROOT / "data/evaluation/culture_retrieval_cases.json")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/culture_retrieval_evaluation.json")
    parser.add_argument("--top-k", type=int, default=settings.KB_TOP_K)
    parser.add_argument("--vector-threshold", type=float, default=settings.KB_VECTOR_SIMILARITY_THRESHOLD)
    parser.add_argument("--rerank-threshold", type=float, default=settings.KB_RERANK_RELEVANCE_THRESHOLD)
    args = parser.parse_args()
    if args.top_k < 1:
        parser.error("--top-k must be positive")

    suite_bytes = args.suite.read_bytes()
    corpus_path = ROOT / "data/data.txt"
    corpus_bytes = corpus_path.read_bytes()
    suite = load_culture_cases(args.suite)
    repository = FoodKnowledgeRepository()
    try:
        def search(query: str, top_k: int):
            return repository.search(
                query,
                top_k=top_k,
                vector_similarity_threshold=args.vector_threshold,
                rerank_relevance_threshold=args.rerank_threshold,
            )

        report = evaluate_culture_cases(suite, search, top_k=args.top_k)
    finally:
        repository.close()

    report["metadata"] = {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "suite_sha256": hashlib.sha256(suite_bytes).hexdigest(),
        "corpus_sha256": hashlib.sha256(corpus_bytes).hexdigest(),
        "chunk_size": settings.KB_CHUNK_SIZE,
        "chunk_overlap": settings.KB_CHUNK_OVERLAP,
        "collection": settings.MILVUS_COLLECTION,
        "embedding_model": settings.EMBEDDING_MODEL,
        "rerank_model": settings.RERANK_MODEL,
        "vector_similarity_threshold": args.vector_threshold,
        "rerank_relevance_threshold": args.rerank_threshold,
    }
    write_culture_report(report, args.output)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
