"""Format selected recipe evidence consistently for retrieval and answers."""

from typing import Any

from ricegod.domain.recipe_ranking import RecipeRequest
from ricegod.infrastructure.recipe_repository import RecipeRepository


def format_recipe_evidence(rows: list[dict[str, Any]], request: RecipeRequest) -> str:
    if request.operation == "similar":
        if not rows:
            return "未找到相似菜谱。"
        lines = []
        for index, row in enumerate(rows, start=1):
            name = row.get("菜名") or row.get("name") or ""
            shared = "、".join(str(value) for value in row.get("shared_ingredients") or [])
            reason = f"共同食材：{shared}" if shared else "基于共享食材关系"
            lines.append(f"相似菜 {index}: {name}\n推荐理由: {reason}\n相似度得分: {row.get('similarity_score', '')}")
        return "\n\n".join(lines) + "\n想看哪一道的详细材料和步骤？"
    normalized = [{**row, "name": row.get("name") or row.get("菜名", "")} for row in rows]
    content = RecipeRepository.format_recipe_context(normalized,
        summary=request.recommendation and not request.detailed)
    if rows and request.recommendation:
        if len(rows) < request.limit:
            content += f"\n本次找到 {len(rows)} 道不同的匹配菜谱，未放宽筛选条件补足数量。"
        if request.requested_count and request.requested_count > request.limit:
            content += f"\n本次最多展示 {request.limit} 道菜谱。"
        if not request.detailed:
            content += "\n你想看哪道菜的详细材料和步骤？"
    return content
