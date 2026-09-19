"""Official PostgreSQL checkpoints with async calls dispatched to threads.

This keeps psycopg database I/O off the event loop and supports the default
Windows Proactor loop as well as sync inspection from verification scripts.
"""

import asyncio
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver, ChannelVersions, Checkpoint, CheckpointMetadata, CheckpointTuple, DeltaChannelHistory,
)
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ricegod.config import settings


def checkpoint_serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=[
        ("ricegod.application.agents.state", name)
        for name in ("Router", "QueryPlan", "PlannedTask", "TaskResult", "RecommendationGroup", "AgentState", "InputState")
    ] + [("ricegod.domain.recipes", "RecipeConstraints"), ("ricegod.domain.recipe_ranking", "RecipeRequest")])


def postgres_dsn() -> str:
    if settings.POSTGRES_DSN:
        return settings.POSTGRES_DSN
    return make_conninfo(host=settings.POSTGRES_HOST, port=settings.POSTGRES_PORT,
                        dbname=settings.POSTGRES_DB, user=settings.POSTGRES_USER,
                        password=settings.POSTGRES_PASSWORD)


class ThreadedPostgresSaver(PostgresSaver):
    """Delegate storage and schema to PostgresSaver; bridge its async methods."""

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return await asyncio.to_thread(self.get_tuple, config)

    async def aput(self, config: RunnableConfig, checkpoint: Checkpoint,
                   metadata: CheckpointMetadata, new_versions: ChannelVersions) -> RunnableConfig:
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config: RunnableConfig, writes: Sequence[tuple[str, Any]],
                          task_id: str, task_path: str = "") -> None:
        await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def alist(self, config: RunnableConfig | None, *, filter: dict[str, Any] | None = None,
                    before: RunnableConfig | None = None, limit: int | None = None) -> AsyncIterator[CheckpointTuple]:
        # The sync iterator holds its cursor/lock while yielding. Consume it in
        # the worker before yielding to callers, so their reads cannot deadlock.
        rows = await asyncio.to_thread(lambda: list(self.list(config, filter=filter, before=before, limit=limit)))
        for item in rows:
            yield item

    async def adelete_thread(self, thread_id: str) -> None:
        await asyncio.to_thread(self.delete_thread, thread_id)

    async def aget_delta_channel_history(self, *, config: RunnableConfig,
                                         channels: Sequence[str]) -> Mapping[str, DeltaChannelHistory]:
        return await asyncio.to_thread(self.get_delta_channel_history, config=config, channels=channels)


@contextmanager
def open_checkpointer() -> Iterator[BaseCheckpointSaver]:
    if settings.CHECKPOINT_BACKEND == "memory":
        yield InMemorySaver(serde=checkpoint_serializer())
        return
    pool = ConnectionPool(postgres_dsn(), min_size=1, max_size=4, open=False, timeout=10,
                          kwargs={"autocommit": True, "row_factory": dict_row,
                                  "prepare_threshold": 0, "connect_timeout": 5,
                                  "options": "-c statement_timeout=10000"})
    try:
        pool.open(wait=True, timeout=10)
        # Serialize schema migrations across application startups. The session lock
        # stays on this connection; setup needs autocommit for concurrent indexes.
        with pool.connection() as conn:
            conn.execute("SELECT pg_advisory_lock(%s)", (731904120,))
            try:
                PostgresSaver(conn, serde=checkpoint_serializer()).setup()
            finally:
                conn.execute("SELECT pg_advisory_unlock(%s)", (731904120,))
        yield ThreadedPostgresSaver(pool, serde=checkpoint_serializer())
    finally:
        pool.close()
