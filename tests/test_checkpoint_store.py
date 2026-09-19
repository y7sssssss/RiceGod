import asyncio
from contextlib import contextmanager
from threading import Event
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from ricegod import bootstrap
from ricegod.config import settings
from ricegod.infrastructure import checkpoint_store


def test_checkpoint_database_io_does_not_block_event_loop(monkeypatch):
    saver = checkpoint_store.ThreadedPostgresSaver(SimpleNamespace())
    started, release = Event(), Event()

    def read(config):
        started.set()
        assert release.wait(2), "Test failed to release the database worker"
        return "checkpoint"

    monkeypatch.setattr(saver, "get_tuple", read)

    async def scenario():
        task = asyncio.create_task(saver.aget_tuple({"configurable": {"thread_id": "test"}}))
        try:
            assert await asyncio.wait_for(asyncio.to_thread(started.wait, 1), 1.5)
            # The loop can still execute this coroutine while the DB call waits.
            assert not task.done()
        finally:
            release.set()
        assert await asyncio.wait_for(task, 1) == "checkpoint"

    asyncio.run(scenario())


def test_explicit_memory_backend_never_opens_postgres(monkeypatch):
    monkeypatch.setattr(settings, "CHECKPOINT_BACKEND", "memory")

    def forbidden(*args, **kwargs):
        raise AssertionError("Memory mode must not create a database pool")

    monkeypatch.setattr(checkpoint_store, "ConnectionPool", forbidden)
    with checkpoint_store.open_checkpointer() as saver:
        assert isinstance(saver, InMemorySaver)


def test_postgres_failure_closes_pool_and_does_not_fall_back(monkeypatch):
    monkeypatch.setattr(settings, "CHECKPOINT_BACKEND", "postgres")
    closed = []

    class Pool:
        def open(self, **kwargs):
            raise RuntimeError("Checkpoint database unavailable")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(checkpoint_store, "ConnectionPool", lambda *args, **kwargs: Pool())
    with pytest.raises(RuntimeError, match="Checkpoint database unavailable"):
        with checkpoint_store.open_checkpointer():
            pytest.fail("Startup must fail instead of switching storage")
    assert closed == [True]


def test_setup_failure_releases_migration_lock_and_pool(monkeypatch):
    monkeypatch.setattr(settings, "CHECKPOINT_BACKEND", "postgres")
    events = []

    class Connection:
        def execute(self, sql, params):
            events.append("unlock" if "unlock" in sql else "lock")

    class Pool:
        def open(self, **kwargs):
            pass

        @contextmanager
        def connection(self):
            yield Connection()

        def close(self):
            events.append("close")

    def fail_setup(self):
        raise RuntimeError("Schema migration failed")

    monkeypatch.setattr(checkpoint_store, "ConnectionPool", lambda *args, **kwargs: Pool())
    monkeypatch.setattr(checkpoint_store.PostgresSaver, "setup", fail_setup)
    with pytest.raises(RuntimeError, match="Schema migration failed"):
        with checkpoint_store.open_checkpointer():
            pytest.fail("Migration must succeed before serving requests")
    assert events == ["lock", "unlock", "close"]


def test_failed_graph_build_and_normal_shutdown_release_checkpoint_resources(monkeypatch):
    events = []
    catalog = SimpleNamespace(client=SimpleNamespace(close=lambda: events.append("catalog_close")))

    @contextmanager
    def checkpoint():
        events.append("checkpoint_open")
        try:
            yield object()
        finally:
            events.append("checkpoint_close")

    monkeypatch.setattr(bootstrap, "RecipeGraphRepository", lambda: catalog)
    monkeypatch.setattr(bootstrap, "RecipeRepository", lambda: object())
    monkeypatch.setattr(bootstrap, "open_checkpointer", checkpoint)

    def fail(*args, **kwargs):
        raise RuntimeError("Graph build failed")

    monkeypatch.setattr(bootstrap, "build_graph", fail)
    with pytest.raises(RuntimeError, match="Graph build failed"):
        bootstrap.create_runtime()
    assert events == ["checkpoint_open", "checkpoint_close", "catalog_close"]

    events.clear()
    monkeypatch.setattr(bootstrap, "build_graph", lambda *args, **kwargs: object())
    monkeypatch.setattr(bootstrap, "MemoryStore", lambda: object())
    monkeypatch.setattr(bootstrap, "MemoryService", lambda store: object())
    runtime = bootstrap.create_runtime()
    assert events == ["checkpoint_open"]
    runtime.close()
    runtime.close()
    assert events == ["checkpoint_open", "checkpoint_close", "catalog_close"]
