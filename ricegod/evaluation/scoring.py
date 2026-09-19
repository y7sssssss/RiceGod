"""Score frozen expectations independently of production extraction/filtering."""

import math
from statistics import mean

from ricegod.evaluation.cases import RecipeExpectation, Turn


def _values(row, field):
    value = row.get(field) or []
    return [str(v) for v in value] if isinstance(value, list) else [str(value)]


def _ingredients(row):
    names = []
    for field in ("主食材", "辅料"):
        for item in row.get(field) or []:
            if isinstance(item, dict):
                names.append(str(item.get("name") or ""))
            elif isinstance(item, (list, tuple)) and item:
                names.append(str(item[0]))
            elif isinstance(item, str):
                names.append(item)
    return names


def satisfies_fields(row, expected: RecipeExpectation):
    if expected.dish_name and _name(row) != expected.dish_name:
        return False
    ingredients = _ingredients(row)
    if any(not any(wanted in name for name in ingredients) for wanted in expected.required):
        return False
    if any(banned in name for banned in expected.excluded for name in ingredients):
        return False
    for field, wanted in (("口味", expected.flavors), ("工艺", expected.methods), ("类型", expected.dish_types)):
        if any(value not in _values(row, field) for value in wanted):
            return False
    if expected.avoid_spicy and (any("辣" in value for value in _values(row, "口味")) or any(
            marker in name for name in ingredients for marker in ("辣椒", "辣油", "辣酱", "泡椒", "剁椒", "小米椒"))):
        return False
    return True


def _name(row):
    return row.get("菜名") or row.get("name")


def conflict_clarification_checks(turn: Turn, body, task_routes):
    """A conflict needs an explanation and a question, without recipe execution."""
    answer = body.get("answer", "")
    return [{"name": "conflict_clarification", "passed": bool(
        turn.conflict_clarification in answer
        and any(marker in answer for marker in ("请问", "？", "?"))
        and not body.get("sources")
        and "recipe_query" not in task_routes
    )}]


def score_turn(turn: Turn, body, state, memories, prior):
    checks = []
    fields = {"returned": 0, "matched": 0}

    def check(name, passed, **details):
        checks.append({"name": name, "passed": bool(passed), **details})

    if turn.routes is not None:
        check("routes", set(body["route"].split("+")) == set(turn.routes))
    recipe_tasks = [task for task in state["plan"].tasks if task.route == "recipe_query"]
    if turn.recipes is not None:
        check("recipe_task_count", len(recipe_tasks) == len(turn.recipes))
        for index, expected in enumerate(turn.recipes):
            candidates = [t for t in recipe_tasks if not expected.scope or expected.scope in t.source_text]
            if not expected.scope:
                candidates = recipe_tasks[index:index + 1]
            check(f"task_{index}_scope", len(candidates) == 1)
            if len(candidates) != 1:
                # Missing tasks must not inflate returned-record satisfaction.
                check(f"task_{index}_count", False)
                continue
            task = candidates[0]
            result = state["task_results"].get(task.id)
            rows = result.records if result is not None else []
            check(f"task_{index}_count", (0 < len(rows) <= expected.count) if expected.allow_fewer else len(rows) == expected.count,
                  expected=expected.count, actual=len(rows))
            extracted = task.constraints
            if expected.count:
                check(f"task_{index}_request_limit", task.recipe_request is not None and task.recipe_request.limit == expected.count)
            if expected.dish_name:
                check(f"task_{index}_dish_name", extracted is not None and extracted.dish_name == expected.dish_name)
            for key, values in (("required_ingredients", expected.required), ("excluded_ingredients", expected.excluded),
                                ("flavors", expected.flavors), ("methods", expected.methods), ("dish_types", expected.dish_types)):
                if values and not (key == "required_ingredients" and expected.dish_name):
                    check(f"task_{index}_{key}", extracted is not None and set(values) <= set(getattr(extracted, key)))
            if expected.avoid_spicy:
                check(f"task_{index}_avoid_spicy", extracted is not None and extracted.avoid_spicy)
            matched = sum(satisfies_fields(row, expected) for row in rows)
            fields["returned"] += len(rows)
            fields["matched"] += matched
            check(f"task_{index}_record_fields", matched == len(rows))
            check(f"task_{index}_answer_lists_records", all(_name(row) and _name(row) in body["answer"] for row in rows))
            check(f"task_{index}_status", result is not None and result.status in (
                ("empty",) if expected.count == 0 else ("ok", "degraded")))
    if turn.selection:
        source = prior.get(turn.selection.from_turn)
        groups = source.get("last_recommendations", []) if source else []
        selection = turn.selection
        expected_name = None
        if selection.group < len(groups) and selection.number <= len(groups[selection.group].records):
            expected_name = _name(groups[selection.group].records[selection.number - 1])
        rows = [row for t in recipe_tasks for row in state["task_results"][t.id].records]
        check("selection", expected_name is not None and state["plan"].selected_recipe_name == expected_name
              and [_name(row) for row in rows] == [expected_name] and expected_name in body["answer"],
              expected_name=expected_name)
    if turn.clarification:
        check("clarification", turn.clarification in body["answer"]
              and bool(state["plan"].recipe_selection_error) and not body["sources"])
    if turn.conflict_clarification:
        checks.extend(conflict_clarification_checks(turn, body, [t.route for t in state["plan"].tasks]))
    if turn.memory_status:
        check("memory_status", body["memory_write"]["status"] == turn.memory_status,
              expected=turn.memory_status, actual=body["memory_write"]["status"])
    if turn.extraction_status:
        check("extraction_status", body["memory_write"]["extraction_status"] == turn.extraction_status)
    contents = [m.content for m in memories]
    for content in turn.memory_contains:
        check("memory_contains_" + content, any(content in value for value in contents))
    for content in turn.memory_absent:
        check("memory_absent_" + content, all(content not in value for value in contents))
    if turn.culture_evidence:
        culture = [state["task_results"][t.id] for t in state["plan"].tasks if t.route == "food_knowledge"]
        check("culture_evidence", bool(culture) and all(r.status == "ok" and r.records and r.sources for r in culture))
    if turn.recipe_dependency:
        recipe_ids = {t.id for t in recipe_tasks}
        culture = [t for t in state["plan"].tasks if t.route == "food_knowledge"]
        check("recipe_dependency", bool(recipe_ids) and bool(culture)
              and all(set(t.depends_on) & recipe_ids for t in culture))
    return checks, fields


def distribution(values):
    if not values:
        return {"samples": 0, "mean_ms": None, "p50_ms": None, "p95_ms": None}
    ordered = sorted(values)
    return {"samples": len(values), "mean_ms": round(mean(values), 3),
            "p50_ms": round(ordered[max(math.ceil(len(values) * .5) - 1, 0)], 3),
            "p95_ms": round(ordered[max(math.ceil(len(values) * .95) - 1, 0)], 3)}


def ratio(passed, total):
    return {"passed": passed, "total": total, "rate": passed / total if total else None}


def summarize(cases):
    completed = [case for case in cases if case.get("performance")]
    events = [event for case in completed for event in case["performance"]["events"]]
    checks = [c for case in cases for c in case["checks"]]
    stage_names = sorted({s["name"] for case in completed for s in case["performance"]["stages"]})
    stages = {}
    for name in stage_names:
        spans = [s for case in completed for s in case["performance"]["stages"] if s["name"] == name]
        stages[name] = {**distribution([s["duration_ms"] for s in spans]),
                        "errors": sum(s["status"] != "ok" for s in spans),
                        "total_work_ms": round(sum(s["duration_ms"] for s in spans), 3)}
    planner = [e for e in events if e["kind"] == "planner"]
    attempted = [e for e in planner if e["mode"] in ("model", "fallback")]
    selected = [c for c in checks if c["name"] == "selection"]
    quantities = [c for c in checks if c["name"].startswith("task_") and c["name"].endswith("_count")]
    memory_checks = [c for c in checks if c["name"].startswith("memory_")]
    returned = sum(c["fields"]["returned"] for c in cases)
    matched = sum(c["fields"]["matched"] for c in cases)
    baselines = [c["baseline"] for c in cases if c.get("baseline")]
    baseline_returned = sum(b["returned"] for b in baselines)
    baseline_matched = sum(b["matched"] for b in baselines)
    baseline_cases = [c for c in cases if c.get("baseline")]
    paths = {}
    for case in completed:
        request_events = case["performance"]["events"]
        if any(e["kind"] == "graph" and e.get("mode") == "direct_memory_operation" for e in request_events):
            path = "direct_memory_operation"
        elif any(e["kind"] == "planner" and e.get("mode") == "selection" for e in request_events):
            path = "recipe_selection"
        else:
            path = "normal_graph"
        paths.setdefault(path, []).append(case["performance"]["total_ms"])
    return {
        "cases": ratio(sum(c["passed"] for c in cases), len(cases)),
        "checks": ratio(sum(c["passed"] for c in checks), len(checks)),
        "returned_record_field_satisfaction": ratio(matched, returned),
        "selection_accuracy": ratio(sum(c["passed"] for c in selected), len(selected)),
        "recipe_quantity_accuracy": ratio(sum(c["passed"] for c in quantities), len(quantities)),
        "memory_checks": ratio(sum(c["passed"] for c in memory_checks), len(memory_checks)),
        "planner_fallback": ratio(sum(e["mode"] == "fallback" for e in attempted), len(attempted)),
        "planner_validation_failure": ratio(sum(e["mode"] == "fallback" and e.get("phase") == "validation" for e in attempted), len(attempted)),
        "task_outcomes": {status: sum(e.get("status") == status for e in events if e["kind"] == "task")
                          for status in ("ok", "empty", "degraded", "failed", "blocked")},
        "planner_modes": {mode: sum(e["mode"] == mode for e in planner) for mode in ("rules", "selection", "model", "fallback")},
        "request_latency": distribution([c["performance"]["total_ms"] for c in completed]),
        "request_latency_by_path": {path: distribution(values) for path, values in paths.items()},
        "client_latency": distribution([c["client_ms"] for c in cases]),
        "timing_coverage": ratio(len(completed), len(cases)),
        "stage_latency": stages,
        "legacy_keyword_baseline": {"cases": len(baselines), "fields": ratio(baseline_matched, baseline_returned)},
        "current_on_baseline_cases": {"cases": len(baseline_cases), "fields": ratio(
            sum(c["fields"]["matched"] for c in baseline_cases), sum(c["fields"]["returned"] for c in baseline_cases))},
    }


def compare_reports(current, previous):
    # Refuse comparisons across different suites, selected cases or execution modes.
    keys = ("suite_sha256", "mode", "recipe_sha256", "case_ids", "repeats")
    different = [key for key in keys if current["metadata"].get(key) != previous.get("metadata", {}).get(key)]
    if different:
        return {"compatible": False, "reason": "Different evaluation identity", "different_fields": different}
    deltas = {}
    for key in ("cases", "returned_record_field_satisfaction", "selection_accuracy", "planner_fallback"):
        before = previous["summary"][key]["rate"]
        after = current["summary"][key]["rate"]
        deltas[key + "_rate_delta"] = after - before if before is not None and after is not None else None
    for key in ("mean_ms", "p50_ms", "p95_ms"):
        before = previous["summary"]["request_latency"][key]
        after = current["summary"]["request_latency"][key]
        deltas["request_latency_" + key + "_delta"] = round(after - before, 3) if before is not None and after is not None else None
    return {"compatible": True, "deltas": deltas,
            "configuration_changed": current["metadata"].get("configuration") != previous["metadata"].get("configuration")}
