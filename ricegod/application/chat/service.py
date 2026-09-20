"""Own one turn: history, recall, graph execution, write receipt, and persistence."""

import asyncio
import json
import logging
from uuid import uuid4
from weakref import WeakValueDictionary
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.types import Overwrite

from ricegod.application.agents.state import QueryPlan, Router
from ricegod.application.chat.models import ChatRequest, ChatResponse, UsedMemory
from ricegod.application.memory.models import MemoryOperationReceipt, MemorySearchResult, MemoryWriteResult
from ricegod.application.memory.confirmation import confirm_memory_write
from ricegod.application.memory.service import MemoryService
from ricegod.infrastructure.memory_store import MemoryStore
from ricegod.config import settings
from ricegod.application.memory.operations import parse_memory_command
from ricegod.application.memory.policy import extraction_skip_reason
from ricegod.application.performance import performance_scope, measure_stage, timed_call, record_event

logger = logging.getLogger(__name__)


class ChatExecutionError(RuntimeError):
    """Graph execution failed; the transport decides how to report it."""

    def __init__(self) -> None:
        super().__init__("Chat execution failed.")


class ChatService:
    def __init__(self, graph, memory_store: MemoryStore | None = None,
                 memory_service: MemoryService | None = None):
        self.graph = graph
        self.memory_store = memory_store
        self.memory_service = memory_service
        # Locks and checkpoints belong to this runtime; the key scopes both by user and conversation.
        self._chat_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

    async def chat(self, request: ChatRequest) -> ChatResponse:
        # Serialize writes to one checkpoint within this process. This is scoping, not authentication.
        key = _thread_key(request.user_id, request.conversation_id)
        lock = self._chat_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._chat_locks[key] = lock
        with performance_scope() as timings:
            try:
                with measure_stage("total"):
                    with measure_stage("lock_wait"):
                        await lock.acquire()
                    distributed_lock = None
                    try:
                        if self.memory_store and hasattr(self.memory_store, "acquire_conversation_lock"):
                            distributed_lock = await timed_call(
                                "distributed_lock_wait",
                                asyncio.to_thread,
                                self.memory_store.acquire_conversation_lock,
                                user_id=request.user_id,
                                conversation_id=request.conversation_id,
                            )
                        response = await self._chat_locked(request)
                    except ChatExecutionError:
                        raise
                    except Exception:
                        logger.exception("Chat turn failed outside graph execution.")
                        raise ChatExecutionError() from None
                    finally:
                        if distributed_lock is not None:
                            await asyncio.to_thread(distributed_lock.release)
                        lock.release()
            finally:
                logger.info("Chat performance: %s", timings.report().model_dump_json())
            response.performance = timings.report()
            return response

    async def _chat_locked(self, request: ChatRequest) -> ChatResponse:
        used_memories: list[MemorySearchResult] = []
        memory_prompt = ""
        recovered_history_messages: list[BaseMessage] = []
        turn_message_id: int | None = None
        logged_message = None
        user_logged = False
        durable_turn = bool(self.memory_store and hasattr(self.memory_store, "begin_turn"))
        if durable_turn:
            turn = await timed_call(
                "user_message_write",
                asyncio.to_thread,
                self.memory_store.begin_turn,
                conversation_id=request.conversation_id,
                user_id=request.user_id,
                request_id=request.request_id,
                content=request.message,
            )
            if turn.get("turn_status") == "completed" and turn.get("turn_response"):
                return ChatResponse.model_validate(turn["turn_response"])
            turn_message_id = int(turn["id"])
            logged_message = turn
            user_logged = True
        config = {"configurable": {"thread_id": _thread_key(request.user_id, request.conversation_id)}}
        try:
            snapshot = await timed_call("checkpoint_read", self.graph.aget_state, config)
            has_checkpoint_history = bool((snapshot.values or {}).get("messages"))
        except Exception:
            logger.exception("Failed to inspect LangGraph checkpoint; skipping DB history recovery.")
            # Conservative fallback: skip DB recovery if checkpoint probing fails.
            has_checkpoint_history = True

        if not has_checkpoint_history and self.memory_store:
            try:
                recent_history = await timed_call("history_recovery", asyncio.to_thread, self.memory_store.get_recent_messages,
                    conversation_id=request.conversation_id,
                    user_id=request.user_id,
                    limit=settings.RECENT_CONTEXT_MESSAGES,
                )
                if turn_message_id is not None:
                    recent_history = [row for row in recent_history if row.get("id") != turn_message_id]
                recovered_history_messages = _records_to_graph_messages(recent_history)
            except Exception:
                logger.exception("Failed to recover recent chat history from PostgreSQL messages table.")
                recovered_history_messages = []

        if self.memory_store and not durable_turn:
            try:
                record = await timed_call("user_message_write", asyncio.to_thread, self.memory_store.add_message,
                    conversation_id=request.conversation_id,
                    user_id=request.user_id,
                    role="user",
                    content=request.message,
                )
                user_logged = True
                if record is not None:
                    logged_message = record.model_dump() if hasattr(record, "model_dump") else record
            except Exception:
                logger.exception("Failed to persist current user message; skipping memory extraction for this turn.")
        receipt = MemoryWriteResult(available=bool(self.memory_service and user_logged))
        command = parse_memory_command(request.message)
        question = request.message
        if command:
            if self.memory_service and user_logged and logged_message:
                try:
                    receipt = await timed_call("memory_operation", self.memory_service.extract_and_store_result,
                        user_id=request.user_id, conversation_id=request.conversation_id,
                        user_message=logged_message)
                except Exception:
                    logger.exception("Explicit memory operation failed before recall.")
                    receipt.failed_count += 1
            if receipt.operation is None:
                receipt.operation = MemoryOperationReceipt(action=command.action, status="failed",
                    message="长期记忆服务不可用、消息未成功记录或操作处理失败，本次未确认修改或删除。")
            if receipt.operation and receipt.operation.status == "applied":
                question = command.remaining_question
            else:
                question = ""
        if self.memory_service:
            try:
                used_memories = await timed_call("memory_recall", asyncio.to_thread, self.memory_service.recall,
                                                user_id=request.user_id, query=request.message)
                memory_prompt = self.memory_service.build_memory_prompt(used_memories)
            except Exception:
                logger.exception("Memory recall failed; continuing with current user message.")

        current_message_id = (f"request:{request.request_id}" if durable_turn else str(uuid4()))
        current_message = HumanMessage(content=request.message, id=current_message_id)
        graph_messages = [current_message]
        context_update = ({"context_history_after": current_message.id}
                          if receipt.operation and receipt.operation.status == "applied" else {})
        if not has_checkpoint_history and recovered_history_messages:
            graph_messages = [*recovered_history_messages, *graph_messages]

        try:
            if command and not question:
                # Pure operations and unresolved targets need no answer-generation
                # model; save the actual receipt while preserving recommendations.
                with measure_stage("memory_confirmation"):
                    answer = confirm_memory_write("", request.message, receipt)
                record_event("graph", mode="direct_memory_operation")
                result = {"messages": [*graph_messages, AIMessage(content=answer)],
                    **context_update,
                    "memory_context": memory_prompt,
                    "user_memories": [item.model_dump(mode="json") for item in used_memories],
                    "question": "", "router": Router(type=["general_query"], logic="explicit memory operation"),
                    "plan": QueryPlan(), "task_results": Overwrite({}), "answer": answer, "sources": []}
                await timed_call("checkpoint_write", self.graph.aupdate_state, config, result, as_node="synthesize_answer")
            else:
                result = await timed_call("graph", self.graph.ainvoke,
                    {
                        "messages": graph_messages,
                        "memory_context": memory_prompt,
                        "user_memories": [item.model_dump(mode="json") for item in used_memories],
                        "question": question,
                        **context_update,
                    },
                    config=config,
                )
        except Exception as exc:
            logger.exception("LangGraph chat invocation failed.")
            if turn_message_id is not None:
                try:
                    await asyncio.to_thread(self.memory_store.fail_turn, turn_message_id)
                except Exception:
                    logger.exception("Failed to mark durable chat turn as failed.")
            raise ChatExecutionError() from exc

        messages = result.get("messages", [])
        answer = str(messages[-1].content) if messages else ""
        try:
            router = Router.model_validate(result.get("router"))
        except Exception:
            router = Router(type=["general_query"], logic="router missing")
        sources = _coerce_sources(result.get("sources"))
        skip = extraction_skip_reason(question) if question else None
        if not command and skip and receipt.available:
            receipt.extraction_status = "skipped"
            receipt.extraction_skip_reason = skip
            logger.info("Memory extraction skipped: %s", skip)
        elif self.memory_service and user_logged and (not command or question):
            try:
                kwargs = {"user_id": request.user_id, "conversation_id": request.conversation_id}
                if command:
                    kwargs.update(user_message=logged_message, extraction_text=question)
                extracted = await timed_call("memory_extraction", self.memory_service.extract_and_store_result, **kwargs)
                if command:
                    receipt.saved.extend(extracted.saved)
                    receipt.unchanged.extend(extracted.unchanged)
                    receipt.failed_count += extracted.failed_count
                    receipt.skipped_count += extracted.skipped_count
                    receipt.extraction_status = extracted.extraction_status
                    receipt.extraction_skip_reason = extracted.extraction_skip_reason
                else:
                    receipt = extracted
            except Exception:
                logger.exception("Memory extraction failed after answer generation")
                receipt.failed_count += 1
        record_event("memory_extraction", status=receipt.extraction_status,
                     skip_reason=receipt.extraction_skip_reason)
        with measure_stage("memory_confirmation"):
            confirmed_answer = confirm_memory_write(answer, request.message, receipt)
        if confirmed_answer != answer and messages and getattr(messages[-1], "id", None):
            try:
                # Replace the generated AI turn by id so response, checkpoint and
                # PostgreSQL history all carry the same storage-confirmed answer.
                await timed_call("checkpoint_write", self.graph.aupdate_state, config, {"messages": [AIMessage(
                    id=messages[-1].id, content=confirmed_answer)], "answer": confirmed_answer},
                    as_node="synthesize_answer")
            except Exception:
                logger.exception("Failed to update checkpoint with memory-confirmed answer.")
        answer = confirmed_answer
        response = ChatResponse(
            answer=answer,
            route=router.type[0] if len(router.type) == 1 else "+".join(router.type),
            logic=router.logic,
            sources=sources,
            used_memories=[
                UsedMemory(id=item.id, type=item.type, content=item.content, score=float(item.score))
                for item in used_memories
            ],
            memory_write=receipt,
        )
        if self.memory_store and durable_turn and turn_message_id is not None:
            await timed_call(
                "turn_completion_write",
                asyncio.to_thread,
                self.memory_store.complete_turn,
                turn_message_id,
                response.model_dump(mode="json"),
                assistant_content=answer,
            )
        elif self.memory_store:
            try:
                await timed_call("assistant_message_write", asyncio.to_thread, self.memory_store.add_message,
                    conversation_id=request.conversation_id,
                    user_id=request.user_id,
                    role="assistant",
                    content=answer,
                )
            except Exception:
                logger.exception("Failed to persist assistant message.")

        return response


def _thread_key(user_id: str, conversation_id: str) -> str:
    return json.dumps([user_id, conversation_id], ensure_ascii=False, separators=(",", ":"))


def _coerce_sources(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return [str(value)]


def _records_to_graph_messages(records: list[dict[str, Any]]) -> list[BaseMessage]:
    messages: list[BaseMessage] = []
    for record in records:
        role = str(record.get("role", "")).strip()
        content = str(record.get("content", "")).strip()
        if not content:
            continue
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))
    return messages
