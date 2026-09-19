import json
from pathlib import Path
from typing import Any
from ricegod.domain.recipes import RecipeConstraints
from ricegod.domain.recipe_ranking import RecipeRequest, rank_recipes


class RecipeRepository:
    """Read and search local recipe JSON data."""

    def __init__(self, data_path: str | Path | None = None) -> None:
        root = Path(__file__).resolve().parents[2]
        self.data_path = Path(data_path) if data_path else root / "data" / "recipe.json"
        self._recipes = self._load_recipes()

    def _load_recipes(self) -> dict[str, dict[str, Any]]:
        with self.data_path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            raise ValueError(f"Recipe data must be a JSON object: {self.data_path}")
        return data

    def search_recipes(
        self, query: str, limit: int = 5, *, constraints: RecipeConstraints | None = None,
        request: RecipeRequest | None = None,
    ) -> list[dict[str, Any]]:
        """Search recipes by name, ingredients, flavor, method, and type."""
        query = query.strip()
        if not query:
            return []

        scored: list[tuple[int, dict[str, Any]]] = []
        for name, recipe in self._recipes.items():
            normalized = self._normalize_recipe(name, recipe)
            if constraints and not constraints.matches(normalized):
                continue
            score = self._score_recipe(query, name, recipe)
            has_positive_filters = constraints and any((constraints.dish_name, constraints.required_ingredients,
                                                        constraints.flavors, constraints.methods, constraints.dish_types))
            is_recommendation = (request is not None and request.recommendation) or any(
                word in query for word in ("推荐", "有哪些", "吃什么", "能做什么"))
            if score <= 0 and not (constraints and (has_positive_filters or is_recommendation)):
                continue
            scored.append((score, normalized))

        if request and request.recommendation:
            return rank_recipes([recipe for _, recipe in scored], constraints or RecipeConstraints(), request)
        scored.sort(key=lambda item: item[0], reverse=True)
        return [recipe for _, recipe in scored[:limit]]

    def _score_recipe(self, query: str, name: str, recipe: dict[str, Any]) -> int:
        score = 0
        if name in query:
            score += 100
        elif query in name:
            score += 80

        for ingredient in self._ingredient_names(recipe):
            if ingredient and ingredient in query:
                score += 25

        for key in ("口味", "工艺", "类型"):
            value = recipe.get(key)
            if isinstance(value, str) and value and value in query:
                score += 20

        return score

    def _ingredient_names(self, recipe: dict[str, Any]) -> list[str]:
        names: list[str] = []
        for key in ("主食材", "辅料"):
            items = recipe.get(key) or []
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, list) and item:
                    names.append(str(item[0]))
                elif isinstance(item, str):
                    names.append(item)
        return names

    def _normalize_recipe(self, name: str, recipe: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": name,
            "主食材": recipe.get("主食材") or [],
            "辅料": recipe.get("辅料") or [],
            "耗时": recipe.get("耗时") or "",
            "口味": recipe.get("口味") or "",
            "工艺": recipe.get("工艺") or "",
            "类型": recipe.get("类型") or "",
            "做法": recipe.get("做法") or "",
        }

    @staticmethod
    def format_recipe_context(results: list[dict[str, Any]], *, summary: bool = False) -> str:
        if not results:
            return "未检索到匹配菜谱。"

        sections: list[str] = []
        for index, recipe in enumerate(results, start=1):
            main_ingredients = RecipeRepository._format_ingredient_list(recipe.get("主食材") or [])
            aux_ingredients = RecipeRepository._format_ingredient_list(recipe.get("辅料") or [])
            lines = [
                        f"菜谱 {index}: {recipe.get('name', '')}",
                        f"类型: {recipe.get('类型', '')}",
                        f"口味: {recipe.get('口味', '')}",
                        f"工艺: {recipe.get('工艺', '')}",
                        f"耗时: {recipe.get('耗时', '')}",
                        f"主食材: {main_ingredients}",
                        f"辅料: {aux_ingredients}",
                    ]
            if recipe.get("推荐理由"):
                lines.append("推荐理由: " + "；".join(recipe["推荐理由"]))
            if not summary:
                lines.append(f"做法: {recipe.get('做法', '')}")
            sections.append("\n".join(lines))
        return "\n\n".join(sections)

    @staticmethod
    def _format_ingredient_list(items: list[Any]) -> str:
        formatted: list[str] = []
        for item in items:
            if isinstance(item, dict) and item.get("name"):
                formatted.append(f"{item['name']}({item.get('amount') or '未记录用量'})")
            elif isinstance(item, list) and len(item) >= 2:
                formatted.append(f"{item[0]}({item[1]})")
            elif isinstance(item, list) and item:
                formatted.append(str(item[0]))
            else:
                formatted.append(str(item))
        return "、".join(formatted) if formatted else "无"
