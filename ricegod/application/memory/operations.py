"""Explicit, evidence-backed single-row memory operations, without LLM writes."""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Literal

from ricegod.application.memory.models import MemoryOperationReceipt, MemoryWriteResult
from ricegod.application.memory.provenance import normalize_text
from ricegod.application.memory.safety import is_sensitive_text

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemoryCommand:
    action: Literal["update", "delete"]
    target: str
    replacement: str = ""
    remaining_question: str = ""
    error: str = ""
    types: tuple[str, ...] = ()
    positive_preference_only: bool = False


_PREFIX = r"(?:请)?(?:你)?(?:帮我|替我)?(?:我想|我要)?\s*"
_DELETE = re.compile(_PREFIX + r"(?:忘掉|忘记|删除|删掉|移除|取消)(?P<target>.+)")
_UPDATE = re.compile(_PREFIX + r"(?:把|将)(?:我(?:的)?)?(?:.+?从)?(?P<target>.+?)(?:改成|改为|更正为|更新为)(?P<new>.+)")
_PREVIOUS = re.compile(_PREFIX + r"我(?:以前|原来|之前)(?P<target>.+?)[，,]\s*(?:现在|以后)(?:改成|改为|是|改为喜欢)?(?P<new>.+)")
_REVOKE = re.compile(_PREFIX + r"我(?:不再喜欢|不喜欢)(?P<target>.+?)了")


def _target_text(value: str) -> str:
    text = value.strip().strip("\"'“”‘’《》 ")
    text = re.sub(r"(?:这|那|这一|那一)?条(?:长期)?(?:记忆|记录|信息)$", "", text)
    text = re.sub(r"(?:的)?(?:长期记忆|记忆|偏好|习惯|记录|信息)$", "", text)
    text = re.sub(r"^(?:我(?:的)?|关于|有关|之前|以前|原来|用户)", "", text)
    return text.strip().strip("\"'“”‘’ ")


def parse_memory_command(message: str) -> MemoryCommand | None:
    # Recipe requests may follow an operation. Keep their exact substring so old
    # quoted restrictions cannot be interpreted as new recipe conditions.
    parts = re.split(r"[。；;\n]|[，,](?=\s*(?:再|然后|并且|并|同时|请)?\s*(?:推荐|用|帮我推荐|给我推荐|告诉我|第二|第三|第\d))", message, maxsplit=1)
    clause = parts[0].strip().strip("。！!？? ")
    tail = parts[1].strip() if len(parts) > 1 else ""
    tail = re.sub(r"^(?:然后|并且|并|同时|再)\s*", "", tail)
    if "？" in message or "?" in message:
        # Questions about operations are not an instruction to execute one.
        return None
    match = _PREVIOUS.fullmatch(clause) or _UPDATE.fullmatch(clause)
    if match:
        target, replacement = _target_text(match["target"]), match["new"].strip().strip("\"'“”‘’ ")
        if re.fullmatch(r"(?:可以|能)(?:吃.{0,30})?(?:了)?", replacement) and re.search(r"不吃|不能吃|不喜欢", target):
            return MemoryCommand("delete", target, remaining_question=tail,
                                 types=("disliked_ingredient", "dietary_preference"))
        return MemoryCommand("update", target, replacement, tail)
    match = _REVOKE.fullmatch(clause)
    if match:
        return MemoryCommand("delete", _target_text(match["target"]), remaining_question=tail,
                             types=("dietary_preference", "liked_cuisine", "recipe_feedback"),
                             positive_preference_only=True)
    match = _DELETE.fullmatch(clause)
    if match:
        target = _target_text(match["target"])
        return MemoryCommand("delete", target, remaining_question=tail)
    if re.match(_PREFIX + r"(?:修改|更正|更新|撤销|清空).*(?:记忆|偏好|习惯|信息|忌口)", clause):
        return MemoryCommand("update", "", error="请明确要修改的旧记忆和新内容，例如：把清淡偏好改成喜欢麻辣。")
    return None


def _match_text(text: str) -> str:
    return re.sub(r"[\s，,。.!！：:；;\"'“”‘’]", "", _target_text(normalize_text(text)))


async def apply_memory_command(service, *, user_id: str, message: dict, command: MemoryCommand) -> MemoryWriteResult:
    result = MemoryWriteResult(extraction_status="skipped", extraction_skip_reason="explicit_memory_operation",
        operation=MemoryOperationReceipt(action=command.action, status="rejected"))
    operation = result.operation
    if (message.get("role") != "user" or message.get("user_id") != user_id
            or type(message.get("id")) is not int or message["id"] <= 0):
        operation.message = "没有可核验的本轮用户消息，未修改长期记忆。"
        return result
    # Parse the actual logged evidence again; callers cannot forge an action.
    if parse_memory_command(str(message.get("content", ""))) != command:
        operation.message = "操作与本轮用户原话不一致，未修改长期记忆。"
        return result
    target = _match_text(command.target)
    if command.error or len(target) < 2 or re.search(r"全部|所有|一切|这条|那条|这个|那个", target):
        operation.status = "ambiguous"
        operation.message = command.error or "请明确要修改或删除哪条长期记忆，例如：忘掉清淡偏好。"
        return result
    if command.action == "update" and (is_sensitive_text(command.replacement)
            or not 2 <= len(command.replacement) <= 300
            or re.search(r"今天|这次|今晚|暂时|仅本次", command.replacement)):
        operation.message = "新内容包含临时条件、敏感信息或不完整，请明确新的长期信息；原记忆保持不变。"
        return result
    try:
        source = await asyncio.to_thread(service.store.get_message, message["id"], user_id=user_id,
                                        conversation_id=message.get("conversation_id", ""))
        if (source is None or source.role != "user" or source.user_id != user_id
                or normalize_text(source.content) != normalize_text(str(message.get("content", "")))):
            operation.message = "本轮操作缺少匹配的数据库用户消息，未修改长期记忆。"
            return result
        records = await asyncio.to_thread(service.store.list_memories, user_id)
        eligible = [record for record in records if record.user_id == user_id
                    and (not command.types or record.type in command.types)
                    and (not command.positive_preference_only or not re.search(r"不喜欢|不爱|不偏好", record.content))]
        # No semantic top-k winner, broad category erasure or model-provided IDs.
        matches = [record for record in eligible if target in _match_text(record.content)]
        if not matches:
            operation.status = "not_found"
            operation.message = "没有找到对应的长期记忆，未执行修改或删除。"
            return result
        if len(matches) != 1:
            operation.status = "ambiguous"
            operation.message = "找到多条可能对应的长期记忆：" + "；".join(record.content for record in matches) + "。请用完整指令指定其中一条，例如：忘掉喜欢清淡这条记忆。"
            return result
        record = matches[0]
        if command.action == "update" and (
                (record.type == "allergy" and ("过敏" not in command.replacement or re.search(
                    r"不.{0,5}过敏|没有.{0,5}过敏", command.replacement)))
                or (record.type == "disliked_ingredient" and not re.search(
                    r"不吃|不能吃|不喜欢|忌口|不放|避免", command.replacement))):
            operation.message = "新内容与原限制记忆的类型不一致。若要撤销限制，请明确删除该条记忆。"
            return result
        if command.action == "delete":
            deleted = await asyncio.to_thread(service.store.delete_memory, record.id, user_id=user_id,
                                             expected_content=record.content)
            if not deleted:
                operation.status = "failed"
                operation.message = "目标记忆已发生变化或删除失败，请重试；本次未确认删除。"
                result.failed_count = 1
                return result
            result.deleted_ids.append(record.id)
        else:
            if service.embedder is None:
                raise RuntimeError("Embedding unavailable for memory update")
            vector = await asyncio.to_thread(service.embedder.embed_query, command.replacement)
            updated = await asyncio.to_thread(service.store.update_memory, memory_id=record.id, user_id=user_id,
                content=command.replacement, embedding=vector, source_message_id=message["id"],
                evidence=command.replacement, expected_content=record.content)
            if updated is None:
                raise RuntimeError("Memory changed before update")
            result.saved.append(updated)
        operation.status = "applied"
        operation.memory_id = record.id
        operation.message = ("已更新长期记忆：" + command.replacement + "。" if command.action == "update"
                             else "已删除长期记忆：" + record.content + "。")
    except Exception:
        logger.exception("Explicit memory operation failed.")
        operation.status = "failed"
        operation.message = "长期记忆操作失败，本次未确认修改或删除，请稍后重试。"
        result.failed_count = 1
    return result
