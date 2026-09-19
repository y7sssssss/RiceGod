"""Request-scoped timing; concurrent task spans retain their own intervals."""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class StageTiming(BaseModel):
    name: str
    start_ms: float
    duration_ms: float
    status: Literal["ok", "error", "cancelled"]
    task_id: str | None = None


class PerformanceReport(BaseModel):
    trace_id: str
    total_ms: float
    stages: list[StageTiming] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)


class TimingCollector:
    def __init__(self):
        self.trace_id = uuid4().hex
        self.started = perf_counter()
        self.stages: list[StageTiming] = []
        self.events: list[dict[str, Any]] = []

    def report(self) -> PerformanceReport:
        total = next((stage.duration_ms for stage in self.stages if stage.name == "total"),
                     round((perf_counter() - self.started) * 1000, 3))
        return PerformanceReport(trace_id=self.trace_id, total_ms=total,
                                 stages=sorted(self.stages, key=lambda s: s.start_ms), events=list(self.events))


_collector: ContextVar[TimingCollector | None] = ContextVar("ricegod_performance", default=None)


@contextmanager
def performance_scope():
    collector = TimingCollector()
    token = _collector.set(collector)
    try:
        yield collector
    finally:
        _collector.reset(token)


@contextmanager
def measure_stage(name: str, *, task_id: str | None = None):
    collector = _collector.get()
    if collector is None:
        yield
        return
    started = perf_counter()
    status = "ok"
    try:
        yield
    except BaseException as exc:
        status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
        raise
    finally:
        collector.stages.append(StageTiming(name=name, task_id=task_id, status=status,
            start_ms=round((started - collector.started) * 1000, 3),
            duration_ms=round((perf_counter() - started) * 1000, 3)))


async def timed_call(name: str, function, *args, **kwargs):
    with measure_stage(name):
        return await function(*args, **kwargs)


def record_event(kind: str, **metadata):
    """Callers supply controlled metadata, never messages, user IDs or credentials."""
    collector = _collector.get()
    if collector is not None:
        collector.events.append({"kind": kind, **metadata})
