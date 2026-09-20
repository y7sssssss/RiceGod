import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ricegod.infrastructure.neo4j_client import Neo4jClient
from ricegod.domain.recipes import RecipeConstraints, SPICY_INGREDIENT_MARKERS, extract_constraints
from ricegod.domain.recipe_ranking import CANDIDATE_LIMIT, RecipeRequest, cypher_ranking
from ricegod.domain.graph_v2 import canonical_ingredient


READ_ONLY_CYPHER_PREFIXES = ("MATCH", "OPTIONAL MATCH", "WITH", "RETURN")
FORBIDDEN_CYPHER_WORDS = (
    "CREATE",
    "MERGE",
    "DELETE",
    "DETACH",
    "SET",
    "DROP",
    "REMOVE",
    "CALL",
    "LOAD",
    "FOREACH",
    "ALTER",
)


@dataclass(frozen=True)
class CypherRequest:
    cypher: str
    params: dict[str, Any]
    source: str


class RecipeGraphRepository:
    """Query recipe knowledge stored in Neo4j."""

    schema_text = """
节点:
- (:Dish {name, cook_time, instructions})
- (:Ingredient {name})
- (:Flavor {name})
- (:CookingMethod {name})
- (:DishType {name})
- (:IngredientAlias {name})
- (:IngredientCategory {name})
- (:Allergen {name})
- (:Source {id, name, url, kind})

关系:
- (:Dish)-[:HAS_MAIN_INGREDIENT {amount_text}]->(:Ingredient)
- (:Dish)-[:HAS_AUX_INGREDIENT {amount_text}]->(:Ingredient)
- (:Dish)-[:HAS_FLAVOR]->(:Flavor)
- (:Dish)-[:USES_METHOD]->(:CookingMethod)
- (:Dish)-[:BELONGS_TO_TYPE]->(:DishType)
- (:IngredientAlias)-[:ALIAS_OF]->(:Ingredient)
- (:Ingredient)-[:IN_CATEGORY]->(:IngredientCategory)-[:IS_A]->(:IngredientCategory)
- (:Ingredient)-[:CONTAINS_ALLERGEN {source_id}]->(:Allergen)
- (:Ingredient)-[:CAN_SUBSTITUTE {context, score, source_id}]->(:Ingredient)
- (:Dish)-[:SUPPORTED_BY]->(:Source)
- (:Ingredient)-[:SUPPORTED_BY]->(:Source)
- (:Allergen)-[:SUPPORTED_BY]->(:Source)
"""

    def __init__(
        self,
        client: Neo4jClient | None = None,
        data_path: str | Path | None = None,
    ) -> None:
        root = Path(__file__).resolve().parents[2]
        self.data_path = Path(data_path) if data_path else root / "data" / "recipe.json"
        self.client = client or Neo4jClient()
        self._recipes = self._load_recipes()
        self.recipe_names = list(self._recipes)
        self.ingredients = self._collect_values(("主食材", "辅料"))
        self.ingredients = sorted(set(self.ingredients).union(
            canonical_ingredient(value) for value in self.ingredients), key=len, reverse=True)
        self.flavors = self._collect_property_values("口味")
        self.methods = self._collect_property_values("工艺")
        self.types = self._collect_property_values("类型")

    def _load_recipes(self) -> dict[str, dict[str, Any]]:
        with self.data_path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            raise ValueError(f"Recipe data must be a JSON object: {self.data_path}")
        return data

    def _collect_values(self, keys: tuple[str, ...]) -> list[str]:
        values: set[str] = set()
        for recipe in self._recipes.values():
            for key in keys:
                for item in recipe.get(key) or []:
                    if isinstance(item, list) and item:
                        values.add(str(item[0]))
                    elif isinstance(item, str):
                        values.add(item)
        return sorted(values, key=len, reverse=True)

    def _collect_property_values(self, key: str) -> list[str]:
        values = {
            value
            for recipe in self._recipes.values()
            if isinstance((value := recipe.get(key)), str) and value
        }
        return sorted(values, key=len, reverse=True)

    def build_predefined_cypher(self, question: str) -> CypherRequest | None:
        """Return a high-confidence template Cypher request when possible."""
        dish_name = self._find_known_value(question, self.recipe_names)
        ingredient = self._find_known_value(question, self.ingredients)
        flavor = self._find_known_value(question, self.flavors)
        method = self._find_known_value(question, self.methods)
        dish_type = self._find_known_value(question, self.types)
        filters = self.extract_constraints(question)
        if filters.excluded_ingredients or filters.excluded_allergens or filters.avoid_spicy or (
            len(filters.required_ingredients) > 1
            or (filters.required_ingredients and (flavor or method or dish_type))
            or (flavor and method and dish_type)
        ):
            return self.build_constrained_cypher(filters)

        if dish_name and self._asks_recipe_steps(question):
            return CypherRequest(
                cypher="""
MATCH (dish:Dish {name: $dish_name})
OPTIONAL MATCH (dish)-[main_rel:HAS_MAIN_INGREDIENT]->(main:Ingredient)
OPTIONAL MATCH (dish)-[aux_rel:HAS_AUX_INGREDIENT]->(aux:Ingredient)
OPTIONAL MATCH (dish)-[:HAS_FLAVOR]->(flavor:Flavor)
OPTIONAL MATCH (dish)-[:USES_METHOD]->(method:CookingMethod)
OPTIONAL MATCH (dish)-[:BELONGS_TO_TYPE]->(dish_type:DishType)
RETURN
  dish.name AS 菜名,
  dish.cook_time AS 耗时,
  dish.instructions AS 做法,
  collect(DISTINCT {name: main.name, raw_name: main_rel.raw_name, amount: main_rel.amount_text}) AS 主食材,
  collect(DISTINCT {name: aux.name, raw_name: aux_rel.raw_name, amount: aux_rel.amount_text}) AS 辅料,
  collect(DISTINCT flavor.name) AS 口味,
  collect(DISTINCT method.name) AS 工艺,
  collect(DISTINCT dish_type.name) AS 类型
""",
                params={"dish_name": dish_name},
                source="predefined_recipe_steps",
            )

        if dish_name and self._asks_ingredients(question):
            return CypherRequest(
                cypher="""
MATCH (dish:Dish {name: $dish_name})
OPTIONAL MATCH (dish)-[main_rel:HAS_MAIN_INGREDIENT]->(main:Ingredient)
OPTIONAL MATCH (dish)-[aux_rel:HAS_AUX_INGREDIENT]->(aux:Ingredient)
RETURN
  dish.name AS 菜名,
  collect(DISTINCT {name: main.name, raw_name: main_rel.raw_name, amount: main_rel.amount_text}) AS 主食材,
  collect(DISTINCT {name: aux.name, raw_name: aux_rel.raw_name, amount: aux_rel.amount_text}) AS 辅料
""",
                params={"dish_name": dish_name},
                source="predefined_recipe_ingredients",
            )

        if flavor and dish_type:
            return self._combined_property_query(
                constraints=[
                    ("HAS_FLAVOR", "Flavor", "flavor", flavor),
                    ("BELONGS_TO_TYPE", "DishType", "dish_type", dish_type),
                ],
                source="predefined_by_flavor_and_type",
            )

        if flavor and method:
            return self._combined_property_query(
                constraints=[
                    ("HAS_FLAVOR", "Flavor", "flavor", flavor),
                    ("USES_METHOD", "CookingMethod", "method", method),
                ],
                source="predefined_by_flavor_and_method",
            )

        if method and dish_type:
            return self._combined_property_query(
                constraints=[
                    ("USES_METHOD", "CookingMethod", "method", method),
                    ("BELONGS_TO_TYPE", "DishType", "dish_type", dish_type),
                ],
                source="predefined_by_method_and_type",
            )

        if flavor:
            return self._property_query("Flavor", "HAS_FLAVOR", "flavor", flavor, "predefined_by_flavor")

        if method:
            return self._property_query("CookingMethod", "USES_METHOD", "method", method, "predefined_by_method")

        if dish_type:
            return self._property_query("DishType", "BELONGS_TO_TYPE", "dish_type", dish_type, "predefined_by_type")

        if dish_name and any(keyword in question for keyword in ("相似", "类似", "差不多")):
            return self.build_similar_recipe_cypher(dish_name)

        if ingredient and any(keyword in question for keyword in ("替代", "代替", "替换")):
            return self.build_substitution_cypher(ingredient)

        if ingredient:
            ingredient = canonical_ingredient(ingredient)
            return CypherRequest(
                cypher="""
MATCH (dish:Dish)-[rel]->(ingredient:Ingredient {name: $ingredient})
WHERE type(rel) IN ["HAS_MAIN_INGREDIENT", "HAS_AUX_INGREDIENT"]
OPTIONAL MATCH (dish)-[:HAS_FLAVOR]->(flavor:Flavor)
OPTIONAL MATCH (dish)-[:USES_METHOD]->(method:CookingMethod)
OPTIONAL MATCH (dish)-[:BELONGS_TO_TYPE]->(dish_type:DishType)
RETURN
  dish.name AS 菜名,
  type(rel) AS 食材关系,
  rel.amount_text AS 用量,
  collect(DISTINCT flavor.name) AS 口味,
  collect(DISTINCT method.name) AS 工艺,
  collect(DISTINCT dish_type.name) AS 类型
LIMIT 10
""",
                params={"ingredient": ingredient},
                source="predefined_by_ingredient",
            )

        return None

    def extract_constraints(self, question: str, memories: list[dict[str, Any]] | None = None) -> RecipeConstraints:
        return extract_constraints(
            question,
            ingredients=self.ingredients,
            flavors=self.flavors,
            methods=self.methods,
            dish_types=self.types,
            dish_names=self.recipe_names,
            memories=memories or [],
        )

    def build_constrained_cypher(self, filters: RecipeConstraints, limit: int = 5) -> CypherRequest:
        """Compose all filters before LIMIT, with parameters shared by the local fallback."""
        predicates: list[str] = []
        params: dict[str, Any] = {"limit": limit}
        if filters.dish_name:
            predicates.append("dish.name = $dish_name")
            params["dish_name"] = filters.dish_name
        if filters.required_ingredients:
            predicates.append("""all(wanted IN $required_ingredients WHERE EXISTS {
  MATCH (dish)-[:HAS_MAIN_INGREDIENT|HAS_AUX_INGREDIENT]->(i:Ingredient)
  WHERE i.name CONTAINS wanted
})""")
            params["required_ingredients"] = [canonical_ingredient(item) for item in filters.required_ingredients]
        excluded = list(filters.excluded_ingredients)
        if filters.avoid_spicy:
            excluded.extend(SPICY_INGREDIENT_MARKERS)
            predicates.append("""NOT EXISTS {
  MATCH (dish)-[:HAS_FLAVOR]->(f:Flavor) WHERE f.name CONTAINS '辣'
}""")
        if excluded:
            predicates.append("""NOT EXISTS {
  MATCH (dish)-[:HAS_MAIN_INGREDIENT|HAS_AUX_INGREDIENT]->(i:Ingredient)
  WHERE any(banned IN $excluded_ingredients WHERE i.name CONTAINS banned)
}""")
            params["excluded_ingredients"] = list(dict.fromkeys(canonical_ingredient(item) for item in excluded))
        if filters.excluded_allergens:
            predicates.append("""NOT EXISTS {
  MATCH (dish)-[:HAS_MAIN_INGREDIENT|HAS_AUX_INGREDIENT]->(:Ingredient)-[:CONTAINS_ALLERGEN]->(a:Allergen)
  WHERE a.name IN $excluded_allergens
}""")
            params["excluded_allergens"] = filters.excluded_allergens
        for key, relationship, label in (
            ("flavors", "HAS_FLAVOR", "Flavor"),
            ("methods", "USES_METHOD", "CookingMethod"),
            ("dish_types", "BELONGS_TO_TYPE", "DishType"),
        ):
            values = getattr(filters, key)
            if values:
                predicates.append(f"""all(wanted IN ${key} WHERE EXISTS {{
  MATCH (dish)-[:{relationship}]->(p:{label}) WHERE p.name = wanted
}})""")
                params[key] = values
        where = "WHERE " + "\nAND ".join(predicates) if predicates else ""
        return CypherRequest(
            cypher=f"""
MATCH (dish:Dish)
{where}
WITH dish ORDER BY dish.name LIMIT $limit
OPTIONAL MATCH (dish)-[main_rel:HAS_MAIN_INGREDIENT]->(main:Ingredient)
OPTIONAL MATCH (dish)-[aux_rel:HAS_AUX_INGREDIENT]->(aux:Ingredient)
OPTIONAL MATCH (dish)-[:HAS_FLAVOR]->(flavor:Flavor)
OPTIONAL MATCH (dish)-[:USES_METHOD]->(method:CookingMethod)
OPTIONAL MATCH (dish)-[:BELONGS_TO_TYPE]->(dish_type:DishType)
RETURN dish.name AS 菜名, dish.cook_time AS 耗时, dish.instructions AS 做法,
  collect(DISTINCT CASE WHEN main.name IS NULL THEN null ELSE {{name: main.name, raw_name: main_rel.raw_name, amount: main_rel.amount_text}} END) AS 主食材,
  collect(DISTINCT CASE WHEN aux.name IS NULL THEN null ELSE {{name: aux.name, raw_name: aux_rel.raw_name, amount: aux_rel.amount_text}} END) AS 辅料,
  collect(DISTINCT flavor.name) AS 口味,
  collect(DISTINCT method.name) AS 工艺,
  collect(DISTINCT dish_type.name) AS 类型
ORDER BY 菜名
""",
            params=params,
            source="predefined_combined_constraints",
        )

    def _combined_property_query(
        self,
        constraints: list[tuple[str, str, str, str]],
        source: str,
    ) -> CypherRequest:
        match_lines = []
        params = {}
        for index, (relationship, label, param_name, value) in enumerate(constraints):
            target_name = f"target_{index}"
            match_lines.append(
                f"MATCH (dish:Dish)-[:{relationship}]->({target_name}:{label} {{name: ${param_name}}})"
            )
            params[param_name] = value
        cypher = f"""
{chr(10).join(match_lines)}
OPTIONAL MATCH (dish)-[:HAS_FLAVOR]->(flavor:Flavor)
OPTIONAL MATCH (dish)-[:USES_METHOD]->(method:CookingMethod)
OPTIONAL MATCH (dish)-[:BELONGS_TO_TYPE]->(dish_type:DishType)
RETURN
  dish.name AS 菜名,
  collect(DISTINCT flavor.name) AS 口味,
  collect(DISTINCT method.name) AS 工艺,
  collect(DISTINCT dish_type.name) AS 类型
LIMIT 10
"""
        return CypherRequest(cypher=cypher, params=params, source=source)

    def query_ranked_candidates(self, filters: RecipeConstraints, request: RecipeRequest) -> list[dict[str, Any]]:
        template = self.build_constrained_cypher(filters, limit=CANDIDATE_LIMIT)
        ranking_clause, ranking_params = cypher_ranking(request)
        cypher = template.cypher.replace("WITH dish ORDER BY dish.name LIMIT $limit", ranking_clause)
        self.validate_read_only_cypher(cypher)
        return self.client.run_query(cypher, {**template.params, **ranking_params,
            "ranking_ingredients": filters.required_ingredients},
            read_only=True, max_records=CANDIDATE_LIMIT, timeout=10.0)

    def build_similar_recipe_cypher(self, dish_name: str, limit: int = 5) -> CypherRequest:
        """Rank other dishes by weighted shared canonical ingredients."""
        return CypherRequest(cypher="""
MATCH (source:Dish {name: $dish_name})-[sourceRel:HAS_MAIN_INGREDIENT|HAS_AUX_INGREDIENT]->(i:Ingredient)
MATCH (candidate:Dish)-[candidateRel:HAS_MAIN_INGREDIENT|HAS_AUX_INGREDIENT]->(i)
WHERE candidate <> source
  AND NOT i.name IN ['盐', '油', '食用油', '水', '生抽', '酱油', '糖', '白砂糖']
WITH candidate,
     sum(CASE WHEN type(sourceRel) = 'HAS_MAIN_INGREDIENT' AND type(candidateRel) = 'HAS_MAIN_INGREDIENT'
              THEN 6 WHEN type(sourceRel) = 'HAS_MAIN_INGREDIENT' THEN 3
              WHEN type(candidateRel) = 'HAS_MAIN_INGREDIENT' THEN 2 ELSE 1 END) AS similarity_score,
     sum(CASE WHEN type(sourceRel) = 'HAS_MAIN_INGREDIENT' AND type(candidateRel) = 'HAS_MAIN_INGREDIENT'
              THEN 1 ELSE 0 END) AS shared_main_count,
     collect(DISTINCT i.name) AS shared_ingredients
RETURN candidate.name AS 菜名, similarity_score, shared_main_count, shared_ingredients
ORDER BY shared_main_count DESC, similarity_score DESC, 菜名
LIMIT $limit
""", params={"dish_name": dish_name, "limit": limit}, source="graph_v2_similar_recipes")

    def build_substitution_cypher(
        self,
        ingredient: str,
        limit: int = 5,
        replacement: str | None = None,
    ) -> CypherRequest:
        canonical = canonical_ingredient(ingredient)
        return CypherRequest(cypher="""
MATCH (ingredient:Ingredient {name: $ingredient})-[rel:CAN_SUBSTITUTE]->(replacement:Ingredient)
WHERE $replacement IS NULL OR replacement.name = $replacement
OPTIONAL MATCH (source:Source {id: rel.source_id})
RETURN ingredient.name AS 原食材, replacement.name AS 替代食材,
       rel.context AS 使用场景, rel.score AS 置信度,
       source.name AS 来源, source.url AS 来源地址
ORDER BY 置信度 DESC, 替代食材
LIMIT $limit
""", params={"ingredient": canonical,
               "replacement": canonical_ingredient(replacement) if replacement else None,
               "limit": limit}, source="graph_v2_substitutions")

    def find_similar_recipes(self, dish_name: str, limit: int = 5) -> list[dict[str, Any]]:
        request = self.build_similar_recipe_cypher(dish_name, limit)
        return self.client.run_query(request.cypher, request.params, read_only=True,
                                     max_records=limit, timeout=10.0)

    def find_ingredient_substitutes(self, ingredient: str, limit: int = 5) -> list[dict[str, Any]]:
        request = self.build_substitution_cypher(ingredient, limit)
        return self.client.run_query(request.cypher, request.params, read_only=True,
                                     max_records=limit, timeout=10.0)

    def _property_query(
        self,
        label: str,
        relationship: str,
        param_name: str,
        value: str,
        source: str,
    ) -> CypherRequest:
        return CypherRequest(
            cypher=f"""
MATCH (dish:Dish)-[:{relationship}]->(target:{label} {{name: ${param_name}}})
OPTIONAL MATCH (dish)-[:HAS_FLAVOR]->(flavor:Flavor)
OPTIONAL MATCH (dish)-[:USES_METHOD]->(method:CookingMethod)
OPTIONAL MATCH (dish)-[:BELONGS_TO_TYPE]->(dish_type:DishType)
RETURN
  dish.name AS 菜名,
  collect(DISTINCT flavor.name) AS 口味,
  collect(DISTINCT method.name) AS 工艺,
  collect(DISTINCT dish_type.name) AS 类型
LIMIT 10
""",
            params={param_name: value},
            source=source,
        )

    def query_by_cypher(
        self,
        cypher: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        clean_cypher = self.clean_cypher(cypher)
        self.validate_read_only_cypher(clean_cypher)
        return self.client.run_query(clean_cypher, params or {}, read_only=True, max_records=10, timeout=10.0)

    @staticmethod
    def validate_read_only_cypher(cypher: str) -> None:
        normalized = re.sub(r"\s+", " ", RecipeGraphRepository.clean_cypher(cypher).strip()).upper()
        if not normalized.startswith(READ_ONLY_CYPHER_PREFIXES):
            raise ValueError("Only read-only Cypher queries are allowed.")
        for word in FORBIDDEN_CYPHER_WORDS:
            if re.search(rf"\b{word}\b", normalized):
                raise ValueError(f"Forbidden Cypher keyword: {word}")

    @staticmethod
    def clean_cypher(cypher: str) -> str:
        text = cypher.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        return text

    @staticmethod
    def format_records(records: list[dict[str, Any]]) -> str:
        if not records:
            return "Neo4j 没有查询到匹配结果。"
        return json.dumps(records, ensure_ascii=False, indent=2)

    @staticmethod
    def _find_known_value(question: str, values: list[str]) -> str | None:
        for value in values:
            if value and len(value) >= 2 and value in question:
                return value
        return None

    @staticmethod
    def _asks_recipe_steps(question: str) -> bool:
        return any(keyword in question for keyword in ("怎么做", "做法", "步骤", "如何做", "怎么烹", "怎么炒"))

    @staticmethod
    def _asks_ingredients(question: str) -> bool:
        return any(keyword in question for keyword in ("食材", "配料", "材料", "需要什么", "用什么"))
