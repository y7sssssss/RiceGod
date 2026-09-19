import argparse
from pathlib import Path

from ricegod.infrastructure.food_knowledge_repository import FoodKnowledgeRepository

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_PATH = PROJECT_ROOT / "data" / "data.txt"


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest food culture text into Milvus.")
    parser.add_argument("--data-path", default=str(DEFAULT_DATA_PATH))
    parser.add_argument("--clear", action="store_true", help="Clear the Milvus collection before ingest.")
    parser.add_argument("--batch-size", default=10, type=int)
    args = parser.parse_args()

    if args.batch_size <= 0:
        raise ValueError("--batch-size must be greater than 0.")

    repository = FoodKnowledgeRepository()
    try:
        total = repository.ingest_file(args.data_path, clear=args.clear, batch_size=args.batch_size)
        print(f"Food knowledge ingestion finished. Total chunks: {total}", flush=True)
    finally:
        repository.close()


if __name__ == "__main__":
    main()
