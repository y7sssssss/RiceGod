from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    LLM_API_KEY: str | None = None
    LLM_BASE_URL: str | None = None
    LLM_MODEL: str = Field(
        default="gpt-4o-mini",
        validation_alias=AliasChoices("LLM_MODEL", "LLM_MODEL_ID"),
    )
    LLM_TIMEOUT_SECONDS: float = Field(default=60.0, gt=0)
    LLM_ENABLE_THINKING: bool | None = None

    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = Field(
        default="neo4j",
        validation_alias=AliasChoices("NEO4J_USER", "NEO4J_USERNAME"),
    )
    NEO4J_PASSWORD: str | None = None
    NEO4J_DATABASE: str = "neo4j"

    MILVUS_HOST: str = "localhost"
    MILVUS_PORT: int = 19530
    MILVUS_COLLECTION: str = "food_knowledge"
    MILVUS_INDEX_TYPE: str = "IVF_FLAT"
    MILVUS_METRIC_TYPE: str = "COSINE"

    EMBEDDING_MODEL: str = "text-embedding-v4"
    EMBEDDING_API_KEY: str | None = None
    EMBEDDING_BASE_URL: str | None = None
    EMBEDDING_DIMENSION: int = 1024

    KB_CHUNK_SIZE: int = 800
    KB_CHUNK_OVERLAP: int = 120
    KB_TOP_K: int = 4
    KB_VECTOR_SIMILARITY_THRESHOLD: float = Field(default=0.35, ge=-1.0, le=1.0)
    KB_RERANK_RELEVANCE_THRESHOLD: float = Field(default=0.50, ge=0.0, le=1.0)
    RERANK_MODEL: str = Field(..., min_length=1)
    RERANK_API_KEY: str | None = None
    RERANK_ENDPOINT: str = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
    RERANK_TOP_N: int = 12
    RERANK_TIMEOUT_SECONDS: float = 10.0

    POSTGRES_DSN: str | None = None
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "ricegod"
    POSTGRES_USER: str = "ricegod"
    POSTGRES_PASSWORD: str = "ricegod"
    CHECKPOINT_BACKEND: Literal["postgres", "memory"] = "postgres"

    MEMORY_TOP_K: int = 5
    MEMORY_RECENT_MESSAGES: int = 8
    MEMORY_SIMILARITY_THRESHOLD: float = 0.9
    MEMORY_RECONCILE_TOP_K: int = 5
    RECENT_CONTEXT_MESSAGES: int = 8
    CONTEXT_RECENT_TURNS: int = Field(default=6, ge=0, le=50)
    CONTEXT_PLANNER_TOKEN_BUDGET: int = Field(default=16000, ge=256)
    CONTEXT_ANSWER_TOKEN_BUDGET: int = Field(default=32000, ge=256)

    AGENT_TASK_TIMEOUT_SECONDS: float = Field(default=45.0, gt=0)
    AGENT_MAX_CONCURRENT_TASKS: int = Field(default=8, ge=1, le=64)
    CYPHER_MAX_ATTEMPTS: int = Field(default=2, ge=1, le=3)

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
