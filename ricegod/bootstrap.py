"""Construct application resources at startup and close them at shutdown."""

import logging
from dataclasses import dataclass, field
from contextlib import ExitStack
from typing import Any

from ricegod.application.agents import conversation
from ricegod.application.agents.workflow import build_graph
from ricegod.application.chat.service import ChatService
from ricegod.application.memory.service import MemoryService
from ricegod.infrastructure.memory_store import MemoryStore
from ricegod.infrastructure.recipe_graph_repository import RecipeGraphRepository
from ricegod.infrastructure.recipe_repository import RecipeRepository
from ricegod.infrastructure.checkpoint_store import open_checkpointer
from ricegod.infrastructure.llm_client import build_chat_model

logger = logging.getLogger(__name__)


@dataclass
class Runtime:
    graph: Any
    recipe_graph_repository: RecipeGraphRepository
    recipe_repository: RecipeRepository
    memory_store: MemoryStore | None = None
    memory_service: MemoryService | None = None
    resources: ExitStack | None = field(default=None, repr=False)
    chat_service: ChatService = field(init=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        self.chat_service = ChatService(self.graph, self.memory_store, self.memory_service)

    def close(self) -> None:
        if self._closed:
            return
        try:
            if self.resources is not None:
                self.resources.close()
            else:
                self.recipe_graph_repository.client.close()
        finally:
            self._closed = True


def create_runtime() -> Runtime:
    catalog = RecipeGraphRepository()
    resources = ExitStack()
    resources.callback(catalog.client.close)
    try:
        # Persistence is required by default; connection/setup failures abort startup.
        checkpointer = resources.enter_context(open_checkpointer())
        local = RecipeRepository()
        graph = build_graph(conversation.general_query,
                            conversation.additional_query, build_chat_model, catalog, local,
                            checkpointer=checkpointer)
        store = service = None
        try:
            store = MemoryStore()
            service = MemoryService(store)
        except Exception:
            logger.exception("Failed to initialize PostgreSQL memory service.")
            store = service = None
        return Runtime(graph, catalog, local, store, service, resources=resources.pop_all())
    except Exception:
        resources.close()
        raise
