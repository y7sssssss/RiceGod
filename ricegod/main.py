"""HTTP contracts, dependency resolution, and application lifecycle."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse

from ricegod.application.chat.models import ChatRequest, ChatResponse, DeleteMemoryResponse
from ricegod.application.chat.service import ChatExecutionError, ChatService
from ricegod.bootstrap import Runtime, create_runtime
from ricegod.infrastructure.memory_store import MemoryStore


STATIC_ROOT = Path(__file__).resolve().parent / "static"


def get_runtime(request: Request) -> Runtime:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="Application has not started.")
    return runtime


def get_chat_service(runtime: Runtime = Depends(get_runtime)) -> ChatService:
    return runtime.chat_service


def get_memory_store(runtime: Runtime = Depends(get_runtime)) -> MemoryStore:
    if runtime.memory_store is None:
        raise HTTPException(status_code=503, detail="PostgreSQL memory store is unavailable.")
    return runtime.memory_store


def health() -> dict[str, str]:
    return {"status": "ok", "service": "ricegod"}


def demo() -> FileResponse:
    return FileResponse(STATIC_ROOT / "index.html", media_type="text/html; charset=utf-8")


async def chat(request: ChatRequest, service: ChatService = Depends(get_chat_service)) -> ChatResponse:
    try:
        return await service.chat(request)
    except ChatExecutionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def list_memories(user_id: str, store: MemoryStore = Depends(get_memory_store)) -> list[dict[str, Any]]:
    return [row.model_dump(mode="json") for row in store.list_memories(user_id)]


def delete_memory(memory_id: int, user_id: str,
                  store: MemoryStore = Depends(get_memory_store)) -> DeleteMemoryResponse:
    return DeleteMemoryResponse(deleted=store.delete_memory(memory_id, user_id=user_id))


def list_messages(user_id: str, conversation_id: str,
                  limit: int = Query(default=200, ge=1, le=1000),
                  store: MemoryStore = Depends(get_memory_store)) -> list[dict[str, Any]]:
    return [row.model_dump(mode="json") for row in store.get_messages(
        conversation_id=conversation_id, user_id=user_id, limit=limit)]


def create_app(runtime_factory: Callable[[], Runtime] = create_runtime) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime = await asyncio.to_thread(runtime_factory)
        app.state.runtime = runtime
        try:
            yield
        finally:
            app.state.runtime = None
            await asyncio.to_thread(runtime.close)

    app = FastAPI(title="RiceGod-V1",
                  description="GraphRAG + RAG recipe assistant backed by Neo4j and Milvus.",
                  version="1.0", lifespan=lifespan)
    app.add_api_route("/", demo, methods=["GET"], include_in_schema=False)
    app.add_api_route("/health", health, methods=["GET"])
    app.add_api_route("/chat", chat, methods=["POST"], response_model=ChatResponse)
    app.add_api_route("/memories", list_memories, methods=["GET"])
    app.add_api_route("/memories/{memory_id}", delete_memory, methods=["DELETE"],
                      response_model=DeleteMemoryResponse)
    app.add_api_route("/messages", list_messages, methods=["GET"])
    return app


app = create_app()
