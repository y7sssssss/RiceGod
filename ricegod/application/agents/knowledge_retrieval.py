"""Retrieve culture evidence using actual records from upstream recipe tasks."""

import asyncio

from ricegod.application.agents.state import PlannedTask, TaskResult
from ricegod.config import settings
from ricegod.infrastructure.food_knowledge_repository import FoodKnowledgeRepository


async def retrieve_knowledge(task: PlannedTask, upstream: list[TaskResult]) -> TaskResult:
    query = task.question
    if upstream:
        names = list(dict.fromkeys(str(row.get("name") or row.get("菜名"))
                                  for result in upstream for row in result.records
                                  if row.get("name") or row.get("菜名")))
        if not names:
            return TaskResult(task_id=task.id, route=task.route, question=query, status="blocked",
                              error_code="missing_recipe_evidence")
        query = "、".join(names) + "的历史、来历与典故。" + task.question
    if not (settings.EMBEDDING_API_KEY or settings.LLM_API_KEY):
        return TaskResult(task_id=task.id, route=task.route, question=query, status="failed",
                          error_code="embedding_unconfigured")

    def search():
        repository = FoodKnowledgeRepository()
        try:
            return repository.search(query, top_k=settings.KB_TOP_K)
        finally:
            repository.close()

    rows = await asyncio.to_thread(search)
    return TaskResult(task_id=task.id, route=task.route, question=query,
                      status="ok" if rows else "empty", records=rows,
                      content=FoodKnowledgeRepository.format_context(rows),
                      sources=list(dict.fromkeys(f"{row['source']}#{row['chunk_index']}" for row in rows
                                                 if row.get("source") and row.get("chunk_index") is not None)))
