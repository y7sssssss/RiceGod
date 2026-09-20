from __future__ import annotations

import asyncio
import logging
import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from ricegod.application.memory.extractor import MemoryExtractor
from ricegod.application.memory.models import MemoryItem, MemoryRecord, MemorySearchResult, MemoryType, MemoryWriteResult
from ricegod.application.memory.prompts import MEMORY_INJECTION_PREFIX
from ricegod.application.memory.safety import filter_sensitive_memories, is_sensitive_text
from ricegod.config import settings
from ricegod.infrastructure.embedding_client import OpenAICompatibleEmbeddingClient
from ricegod.infrastructure.memory_store import MemoryStore
from ricegod.infrastructure.llm_client import build_chat_model
from ricegod.application.memory.provenance import ground_memory, latest_user_messages
from ricegod.application.memory.policy import extraction_skip_reason
from ricegod.application.memory.operations import apply_memory_command, parse_memory_command
from ricegod.application.performance import timed_call


logger = logging.getLogger(__name__)


class MemoryService:
    """Orchestrates extraction, reconciliation, retrieval, and prompt injection."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store
        self.extractor = MemoryExtractor()
        self.similarity_threshold = settings.MEMORY_SIMILARITY_THRESHOLD
        self.top_k = settings.MEMORY_TOP_K
        self.recent_messages = settings.MEMORY_RECENT_MESSAGES
        self.embedder: OpenAICompatibleEmbeddingClient | None
        try:
            self.embedder = OpenAICompatibleEmbeddingClient()
        except Exception:
            logger.exception("Failed to initialize embedding client; memory recall will use fallback ordering.")
            self.embedder = None

    async def extract_and_store(self, *, user_id: str, conversation_id: str) -> list[MemoryRecord]:
        """Compatibility API for callers that only need added/updated records."""
        return (await self.extract_and_store_result(user_id=user_id, conversation_id=conversation_id)).saved

    async def extract_and_store_result(self, *, user_id: str, conversation_id: str,
                                       user_message: dict[str, Any] | None = None,
                                       extraction_text: str | None = None) -> MemoryWriteResult:
        receipt = MemoryWriteResult()
        try:
            await self._extract_and_store(user_id=user_id, conversation_id=conversation_id, receipt=receipt,
                                          user_message=user_message, extraction_text=extraction_text)
        except Exception:
            # Preserve any successful operations before a later extraction/write fails.
            logger.exception("Memory persistence failed.")
            receipt.failed_count += 1
        return receipt

    async def _extract_and_store(self, *, user_id: str, conversation_id: str,
                                 receipt: MemoryWriteResult, user_message: dict[str, Any] | None = None,
                                 extraction_text: str | None = None) -> None:
        recent_messages = ([user_message] if user_message else await asyncio.to_thread(
            self.store.get_recent_messages, conversation_id, self.recent_messages, user_id=user_id))
        scoped_messages = [message for message in recent_messages
                           if message.get("user_id", user_id) == user_id
                           and message.get("conversation_id", conversation_id) == conversation_id]
        user_messages = latest_user_messages(scoped_messages)
        if not user_messages:
            receipt.available = self.embedder is not None
            return
        text = str(user_messages[0].get("content", ""))
        command = parse_memory_command(text) if extraction_text is None else None
        if command:
            operation_result = await apply_memory_command(self, user_id=user_id,
                message=user_messages[0], command=command)
            for name in MemoryWriteResult.model_fields:
                setattr(receipt, name, getattr(operation_result, name))
            return
        if extraction_text is not None:
            if extraction_text not in text:
                raise ValueError("Extraction text must come from the current user message")
            user_messages = [{**user_messages[0], "content": extraction_text}]
            text = extraction_text
        skip = extraction_skip_reason(text)
        if skip:
            receipt.extraction_status = "skipped"
            receipt.extraction_skip_reason = skip
            return
        if not self.embedder:
            receipt.available = False
            return
        receipt.extraction_status = "completed"
        extracted = await self.extractor.extract(user_messages)
        grounded = [item for candidate in extracted.memories
                    if (item := ground_memory(candidate, user_messages)) is not None]
        candidate_memories = filter_sensitive_memories(grounded)
        receipt.skipped_count += len(extracted.memories) - len(candidate_memories)

        changed = receipt.saved
        for candidate in candidate_memories:
            try:
                candidate_embedding = await asyncio.to_thread(self.embedder.embed_query, candidate.content)
            except Exception:
                logger.exception("Candidate memory embedding failed.")
                receipt.failed_count += 1
                continue
            related_memories = await asyncio.to_thread(self.recall,
                user_id=user_id,
                query=candidate.content,
                top_k=settings.MEMORY_RECONCILE_TOP_K,
                include_constraints=False,
                query_embedding=candidate_embedding,
            )
            # Compare only facts of the same type; exclusions must not be treated
            # as duplicates of ordinary preferences and lose complete recall.
            related_memories = [memory for memory in related_memories if memory.type == candidate.type]
            # Only exact, scoped stored content supports an "already saved" claim.
            exact = next((memory for memory in related_memories
                          if memory.user_id == user_id and memory.content == candidate.content), None)

            if not settings.LLM_API_KEY:
                fallback_record = await asyncio.to_thread(self._fallback_store_candidate,
                    user_id=user_id,
                    candidate=candidate,
                    candidate_embedding=candidate_embedding,
                    related_memories=related_memories,
                    receipt=receipt,
                )
                if fallback_record:
                    changed.append(fallback_record)
                continue

            tool_changed: list[MemoryRecord] = []

            @tool
            def add_memory(type: str, content: str, reason: str = "") -> str:
                """Add a new long-term memory when the candidate is useful and not covered by existing memories."""
                normalized = " ".join((content or "").split())
                if is_sensitive_text(normalized):
                    return "Skipped: content is empty or sensitive."
                type_: MemoryType = candidate.type
                # The tool chooses whether to add this candidate; it cannot expand
                # it into other facts from the dialogue or another memory type.
                normalized = " ".join(candidate.content.split())
                try:
                    record = self.store.add_memory(
                        user_id=user_id,
                        type_=type_,
                        content=normalized,
                        embedding=candidate_embedding,
                        source_message_id=candidate.source_message_id,
                        evidence=candidate.evidence,
                    )
                except Exception:
                    logger.exception("Failed to add memory via reconcile tool.")
                    receipt.failed_count += 1
                    return "Skipped: add_memory failed."
                tool_changed.append(record)
                return f"Added memory {record.id}. Reason: {reason}"

            @tool
            def no_op(reason: str = "") -> str:
                """Do nothing when the candidate is duplicate, uncertain, temporary, sensitive, or should not change memory."""
                if exact is not None:
                    receipt.unchanged.append(MemoryRecord.model_validate(exact.model_dump()))
                return f"No operation. Reason: {reason}"

            tools = [add_memory, no_op]
            try:
                model = self._build_reconcile_model().bind_tools(tools)
                prompt_messages = self._build_reconcile_messages(
                    candidate=candidate,
                    related_memories=related_memories,
                    recent_messages=user_messages,
                )
                response = await timed_call("memory_reconcile_model", model.ainvoke, prompt_messages)
                tool_calls = getattr(response, "tool_calls", []) or []
                if not tool_calls:
                    retry_messages = [
                        *prompt_messages,
                        HumanMessage(
                            content=(
                                "你刚才没有调用工具。请必须且只能调用 add_memory 或 no_op，"
                                "不要直接回复自然语言。"
                            )
                        ),
                    ]
                    response = await timed_call("memory_reconcile_model", model.ainvoke, retry_messages)
                    tool_calls = getattr(response, "tool_calls", []) or []

                if not tool_calls:
                    fallback_record = await asyncio.to_thread(self._fallback_store_candidate,
                        user_id=user_id,
                        candidate=candidate,
                        candidate_embedding=candidate_embedding,
                        related_memories=related_memories,
                        receipt=receipt,
                    )
                    if fallback_record:
                        changed.append(fallback_record)
                    continue

                tool_call = tool_calls[0]
                tool_name = tool_call.get("name")
                tool_args = tool_call.get("args", {}) or {}
                tool_map = {item.name: item for item in tools}
                if tool_name in {"update_memory", "delete_memory"}:
                    receipt.skipped_count += 1
                    continue
                selected_tool = tool_map.get(str(tool_name))
                if not selected_tool:
                    logger.warning("Memory reconcile returned unknown tool: %s", tool_name)
                    fallback_record = await asyncio.to_thread(self._fallback_store_candidate,
                        user_id=user_id,
                        candidate=candidate,
                        candidate_embedding=candidate_embedding,
                        related_memories=related_memories,
                        receipt=receipt,
                    )
                    if fallback_record:
                        changed.append(fallback_record)
                    continue

                before = (len(receipt.saved), len(receipt.deleted_ids), len(receipt.unchanged), receipt.failed_count)
                await asyncio.to_thread(selected_tool.invoke, tool_args)
                changed.extend(tool_changed)
                if before == (len(receipt.saved), len(receipt.deleted_ids), len(receipt.unchanged), receipt.failed_count):
                    receipt.skipped_count += 1
            except Exception:
                logger.exception("Memory reconcile tool calling failed; using fallback memory storage.")
                fallback_record = await asyncio.to_thread(self._fallback_store_candidate,
                    user_id=user_id,
                    candidate=candidate,
                    candidate_embedding=candidate_embedding,
                    related_memories=related_memories,
                    receipt=receipt,
                )
                if fallback_record:
                    changed.append(fallback_record)

    def recall(
        self, *, user_id: str, query: str, top_k: int | None = None, include_constraints: bool = True,
        query_embedding: list[float] | None = None,
    ) -> list[MemorySearchResult]:
        k = top_k or self.top_k
        if not query.strip():
            return []
        recalled = None
        if query_embedding is not None or self.embedder:
            try:
                if query_embedding is None:
                    query_embedding = self.embedder.embed_query(query)
                recalled = self.store.search_memories(user_id=user_id, query_embedding=query_embedding, top_k=k)
            except Exception as exc:
                logger.warning("Semantic memory recall unavailable (%s); using stored memories.", type(exc).__name__)
        if recalled is None:
            recalled = [
                MemorySearchResult(
                    **memory.model_dump(),
                    score=0.0,
                )
                for memory in self.store.list_memories(user_id)[:k]
            ]
        if not include_constraints:
            return recalled
        constraints = [MemorySearchResult(**memory.model_dump(), score=0.0)
                       for memory in self.store.get_constraint_memories(user_id)]
        # Safety restrictions can exceed top-k; their zero scores are not semantic similarity claims.
        by_id = {memory.id: memory for memory in constraints}
        for memory in recalled:
            by_id[memory.id] = memory
        return list(by_id.values())

    @staticmethod
    def build_memory_prompt(memories: list[MemorySearchResult]) -> str:
        if not memories:
            return ""
        lines = [f"- [{item.type}] {item.content}" for item in memories]
        return MEMORY_INJECTION_PREFIX.format(memory_lines="\n".join(lines))

    def _build_reconcile_model(self) -> ChatOpenAI:
        return build_chat_model(temperature=0)

    def _fallback_store_candidate(
        self,
        *,
        user_id: str,
        candidate: MemoryItem,
        candidate_embedding: list[float],
        related_memories: list[MemorySearchResult],
        receipt: MemoryWriteResult | None = None,
    ) -> MemoryRecord | None:
        if related_memories and related_memories[0].score >= self.similarity_threshold:
            if receipt is not None:
                exact = next((memory for memory in related_memories
                              if memory.user_id == user_id and memory.content == candidate.content), None)
                if exact is not None:
                    receipt.unchanged.append(MemoryRecord.model_validate(exact.model_dump()))
                else:
                    receipt.skipped_count += 1
            return None
        try:
            return self.store.add_memory(
                user_id=user_id,
                type_=candidate.type,
                content=candidate.content,
                embedding=candidate_embedding,
                source_message_id=candidate.source_message_id,
                evidence=candidate.evidence,
            )
        except Exception:
            logger.exception("Failed to add memory via fallback storage.")
            if receipt is not None:
                receipt.failed_count += 1
            return None

    @staticmethod
    def _build_reconcile_messages(
        *,
        candidate: MemoryItem,
        related_memories: list[MemorySearchResult],
        recent_messages: list[dict[str, Any]],
    ) -> list[SystemMessage | HumanMessage]:
        user_messages = [{"id": message["id"], "content": message["content"]}
                         for message in recent_messages if message.get("role") == "user" and message.get("id")]
        related_lines = [
            f"- id={item.id}; type={item.type}; score={float(item.score):.4f}; content={item.content}"
            for item in related_memories
        ]
        related_text = "\n".join(related_lines) if related_lines else "无相关长期记忆。"
        dialogue_text = json.dumps(user_messages, ensure_ascii=False)
        system_prompt = """
你是长期记忆新增去重器。你必须且只能调用 add_memory 或 no_op，不要直接回复自然语言。

工具选择规则（当前为普通事实提取，不是修改旧记忆的操作流程）：
- 新事实且旧记忆没有覆盖它：调用 add_memory。
- 候选记忆可能替换或修正已有记忆但没有明确操作：调用 no_op，不更新或删除旧记录。
- 不要调用 update_memory 或 delete_memory；更正和遗忘由独立操作流程处理。
- 候选记忆重复、不确定、临时、敏感或不该保存：调用 no_op。
- 一条记录只保留一种类型；不得把不同类型的事实合并。add_memory 的 type 必须与候选类型一致。
- 新增只保存当前候选的用户原话证据；工具 content 不能扩展或拼接其他事实。
- 两条独立偏好使用 add_memory；替换旧事实需要明确更正操作，不合并不同偏好。
- 不更新或删除 allergy 等限制记忆。
- 如果不确定，调用 no_op。
""".strip()
        human_prompt = f"""
最近对话：
{dialogue_text}

候选记忆：
- type={candidate.type}
- content={candidate.content}
- source_message_id={candidate.source_message_id}
- evidence={candidate.evidence}

相关已有长期记忆：
{related_text}
""".strip()
        return [SystemMessage(content=system_prompt), HumanMessage(content=human_prompt)]
