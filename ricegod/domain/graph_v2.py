"""Curated Graph V2 entity normalization and provenance rules."""

from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any


CONFIG_PATH = Path(__file__).resolve().parents[2] / "data" / "graph_v2.json"


@lru_cache(maxsize=1)
def graph_v2_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def clean_ingredient_name(name: str) -> str:
    return re.sub(r"\s+", "", str(name or "").strip())


@lru_cache(maxsize=1)
def _form_aliases() -> dict[str, str]:
    config = graph_v2_config()
    values = set(config.get("aliases", {}).values())
    for group in ("categories", "allergens"):
        for members in config.get(group, {}).values():
            values.update(members)
    aliases = config.get("aliases", {})
    prefixes = ("新鲜", "去皮", "熟", "生")
    suffixes = ("丝", "片", "块", "丁", "泥", "末", "碎", "段", "粒", "条", "汁", "蓉", "茸")
    forms: dict[str, str] = {}
    for base in values:
        canonical = aliases.get(base, base)
        forms.update({base + suffix: canonical for suffix in suffixes})
        forms.update({prefix + base: canonical for prefix in prefixes})
        forms.update({prefix + base + suffix: canonical for prefix in prefixes for suffix in suffixes})
    return forms


def canonical_ingredient(name: str) -> str:
    cleaned = clean_ingredient_name(name)
    aliases = graph_v2_config().get("aliases", {})
    if cleaned in aliases:
        return aliases[cleaned]
    # Normalize only explicit preparation forms around a curated base name.
    # Do not strip arbitrary words because, for example, peanut oil is not the
    # same entity as peanut for allergen provenance purposes.
    return _form_aliases().get(cleaned, cleaned)


def categories_for_ingredient(name: str) -> list[str]:
    canonical = canonical_ingredient(name)
    categories = graph_v2_config().get("categories", {})
    return [category for category, members in categories.items() if canonical in members]


def allergens_for_ingredient(name: str) -> list[str]:
    canonical = canonical_ingredient(name)
    allergens = graph_v2_config().get("allergens", {})
    return [allergen for allergen, members in allergens.items() if canonical in members]


def mentioned_allergens(text: str) -> list[str]:
    """Only return explicitly named allergens; never infer an allergy from preference text."""
    result = []
    for allergen in graph_v2_config().get("allergens", {}):
        if allergen in text and ("过敏" in text or any(word in text for word in ("不能吃", "不要", "忌口", "排除"))):
            result.append(allergen)
    return result


def substitution_rows() -> list[dict[str, Any]]:
    rows = []
    for item in graph_v2_config().get("substitutions", []):
        row = {**item, "from": canonical_ingredient(item["from"]), "to": canonical_ingredient(item["to"])}
        rows.append(row)
        if item.get("bidirectional"):
            rows.append({**row, "from": row["to"], "to": row["from"], "bidirectional": True})
    return rows
