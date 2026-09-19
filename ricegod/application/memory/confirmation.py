"""Render memory acknowledgements exclusively from completed storage receipts."""

import re

from ricegod.application.memory.models import MemoryWriteResult
from ricegod.application.memory.operations import parse_memory_command


MEMORY_PENDING_PROMPT = (
    "长期记忆写入尚未执行。不要宣称已经记住、保存、更新、删除或忘记用户信息，"
    "也不要承诺以后一定会记住。只回答当前问题；写入确认由后续流程提供。"
)
CLAIM = re.compile(r"(?:已(?:经)?(?:记住|记下|保存|存入|记录|更新|忘记|删除)|"
                   r"已(?:经)?(?:将|把).{0,50}(?:记住|记下|保存|存入|记录|更新|忘记|删除)|"
                   r"我(?:已经)?记住|记住.{0,15}了|记下了|保存好了|(?:我会|我将|以后会)(?:一直)?记住)")


def strip_memory_claims(answer: str) -> tuple[str, bool]:
    """Keep unconfirmed operations out of even the initial graph checkpoint."""
    pieces = re.split(r"(?<=[。！？!?\n])", answer)
    claimed = any(CLAIM.search(piece) for piece in pieces)
    clean = "".join(piece for piece in pieces if not CLAIM.search(piece)).strip()
    return clean, claimed


def confirm_memory_write(answer: str, question: str, receipt: MemoryWriteResult) -> str:
    # Defensive guard also covers callers that use a custom graph.
    clean, claimed = strip_memory_claims(answer)
    if receipt.operation:
        # The operation renderer is based on completed writes or precise reasons
        # why nothing changed, including ambiguous and absent targets.
        ordinary = receipt.model_copy(update={"operation": None,
            "saved": [record for record in receipt.saved if record.id != receipt.operation.memory_id],
            "deleted_ids": []})
        extra = confirm_memory_write(clean, "", ordinary) if (ordinary.saved or ordinary.unchanged or ordinary.failed_count) else clean
        return "\n\n".join(part for part in (extra, receipt.operation.message) if part) or "未确认长期记忆操作。"
    explicit = bool(parse_memory_command(question) or re.search(
        r"记住|记下|长期记忆|忘记|忘掉|删除.{0,12}记忆|保存.{0,12}(?:偏好|习惯|信息)", question))
    confirmations: list[str] = []
    if receipt.saved:
        contents = list(dict.fromkeys(record.content for record in receipt.saved))
        confirmations.append("已保存长期记忆：" + "；".join(contents) + "。")
    if receipt.deleted_ids:
        confirmations.append(f"已删除 {len(receipt.deleted_ids)} 条长期记忆。")
    if receipt.unchanged and (explicit or claimed):
        confirmations.append("已有长期记忆中保存了：" + "；".join(
            dict.fromkeys(record.content for record in receipt.unchanged)) + "。")
    if receipt.failed_count or not receipt.available:
        if explicit or claimed or confirmations:
            confirmations.append("本次有长期记忆未能保存，暂时不能确认这些信息会在后续对话中保留。")
    elif receipt.skipped_count:
        if explicit or claimed or confirmations:
            confirmations.append("其余信息未确认写入长期记忆。")
    elif not confirmations and (explicit or claimed):
        confirmations.append("本次未确认有信息写入或删除长期记忆。")
    return "\n\n".join([part for part in (clean, *confirmations) if part]) or "收到你的要求。"
