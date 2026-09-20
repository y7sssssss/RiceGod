"""Run one versioned suite through real chat handlers, offline or with live models."""

import argparse
import copy
import hashlib
import json
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from langsmith import tracing_context

from ricegod.application.chat.service import _thread_key
from ricegod.config import settings
from ricegod.evaluation.cases import EvaluationSuite
from ricegod.evaluation.offline import create_offline_runtime
from ricegod.evaluation.scoring import compare_reports, score_turn, satisfies_fields, summarize
from ricegod.evaluation.reporting import write_readable_report
from ricegod.main import create_app
from scripts.verification_cleanup import cleanup_verification_rows

ROOT = Path(__file__).resolve().parents[1]


def evaluate(suite_path: Path, mode: str, output: Path, *, repeats=1, scenario_ids=None, compare=None):
    suite_bytes = suite_path.read_bytes()
    suite = EvaluationSuite.model_validate_json(suite_bytes)
    scenarios = [s for s in suite.scenarios if scenario_ids is None or s.id in scenario_ids]
    if scenario_ids and set(scenario_ids) - {s.id for s in suite.scenarios}:
        raise ValueError("Unknown scenario id")
    selected = [t.id for s in scenarios for t in s.turns if mode in t.modes]
    if not selected:
        raise ValueError("No applicable turns selected")
    if mode == "live" and (not settings.LLM_API_KEY or settings.CHECKPOINT_BACKEND != "postgres"):
        raise ValueError("Live evaluation requires configured model and PostgreSQL checkpoints")
    previous_key = settings.LLM_API_KEY
    if mode == "offline":
        settings.LLM_API_KEY = None
    app = create_app(runtime_factory=create_offline_runtime) if mode == "offline" else create_app()
    report = {"metadata": {"suite_version": suite.version, "suite_sha256": hashlib.sha256(suite_bytes).hexdigest(),
        "mode": mode, "case_ids": selected, "repeats": repeats, "started_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Frozen field/workflow checks; offline uses fixture memory and local recipes. No subjective taste or factual-answer scoring.",
        "fixture_history": "Long-history turns are seeded fixtures, not paid model conversations.",
        "configuration": {"model": settings.LLM_MODEL if mode == "live" else None,
            "embedding_model": settings.EMBEDDING_MODEL if mode == "live" else None,
            "context_turns": settings.CONTEXT_RECENT_TURNS,
            "context_budgets": [settings.CONTEXT_PLANNER_TOKEN_BUDGET, settings.CONTEXT_ANSWER_TOKEN_BUDGET],
            "planning_architecture": "unified_task_planner", "llm_thinking": settings.LLM_ENABLE_THINKING,
            "task_timeout": settings.AGENT_TASK_TIMEOUT_SECONDS},
        "packages": {p: version(p) for p in ("langgraph", "langchain-core", "langgraph-checkpoint-postgres")}},
        "cases": [], "test_rows_cleaned": False}
    source = hashlib.sha256()
    for path in sorted((ROOT / "ricegod").rglob("*.py")):
        source.update(str(path.relative_to(ROOT)).encode("utf-8"))
        source.update(path.read_bytes())
    report["metadata"]["code_sha256"] = source.hexdigest()
    users, keys = [], set()
    try:
        with tracing_context(enabled=False), TestClient(app) as client:
            runtime = app.state.runtime
            if runtime.memory_store is None or runtime.memory_service is None:
                raise RuntimeError("Evaluation memory service unavailable")
            local = runtime.recipe_repository
            report["metadata"].update(recipe_sha256=hashlib.sha256(local.data_path.read_bytes()).hexdigest(),
                                      recipe_count=len(local._recipes))
            for repeat in range(repeats):
                for scenario in scenarios:
                    turns = [t for t in scenario.turns if mode in t.modes]
                    if not turns:
                        continue
                    user = "fixed-eval-" + uuid4().hex
                    users.append(user)
                    prior = {}
                    for seed in scenario.seeds:
                        record = runtime.memory_store.add_message("seed", user, "user", seed.content)
                        runtime.memory_store.add_memory(user_id=user, type_=seed.type, content=seed.content,
                            embedding=runtime.memory_service.embedder.embed_query(seed.content),
                            source_message_id=record.id, evidence=seed.content)
                    for turn in turns:
                        key = _thread_key(user, turn.conversation)
                        keys.add(key)
                        cfg = {"configurable": {"thread_id": key}}
                        if turn.history_fixture_turns:
                            fixtures = []
                            for i in range(turn.history_fixture_turns):
                                for role, text in (("user", f"历史寒暄{i}，谢谢"), ("assistant", f"历史回复{i}，不客气")):
                                    runtime.memory_store.add_message(turn.conversation, user, role, text)
                                    fixtures.append((HumanMessage if role == "user" else AIMessage)(content=text))
                            runtime.graph.update_state(cfg, {"messages": fixtures}, as_node="synthesize_answer")
                        case = {"id": turn.id, "scenario": scenario.id, "repeat": repeat + 1,
                                "question": turn.message, "checks": [], "fields": {"returned": 0, "matched": 0}}
                        started = perf_counter()
                        body = None
                        try:
                            response = client.post("/chat", json={"user_id": user,
                                "conversation_id": turn.conversation, "message": turn.message})
                            case["client_ms"] = round((perf_counter() - started) * 1000, 3)
                            response.raise_for_status()
                            body = response.json()
                            state = runtime.graph.get_state(cfg).values
                            memories = runtime.memory_store.list_memories(user)
                            case["checks"], case["fields"] = score_turn(turn, body, state, memories, prior)
                            case["checks"].append({"name": "history_matches", "passed": body["answer"]
                                == state["answer"] == runtime.memory_store.get_messages(turn.conversation, user_id=user)[-1].content})
                            performance = body.get("performance")
                            case["checks"].append({"name": "timing_present", "passed": bool(performance and performance["stages"])})
                            case["performance"] = performance
                            case["route"] = body["route"]
                            case["answer"] = body["answer"]
                            case["sources"] = body["sources"]
                            case["task_summary"] = [{"id": task.id, "route": task.route,
                                "source_text": task.source_text, "depends_on": task.depends_on,
                                "constraints": task.constraints.model_dump() if task.constraints else None,
                                "record_names": [r.get("菜名") or r.get("name") for r in state["task_results"][task.id].records],
                                "records": state["task_results"][task.id].records,
                                "status": state["task_results"][task.id].status} for task in state["plan"].tasks]
                            prior[turn.id] = copy.deepcopy(state)
                            if turn.baseline and mode == "offline" and turn.recipes and len(turn.recipes) == 1:
                                rows = local.search_recipes(turn.message, limit=max(turn.recipes[0].count, 1))
                                case["baseline"] = {"returned": len(rows),
                                    "matched": sum(satisfies_fields(r, turn.recipes[0]) for r in rows),
                                    "record_names": [r["name"] for r in rows],
                                    "definition": "Legacy keyword scorer, same local data, no hard filters or personalized ranking."}
                        except Exception as exc:
                            case.setdefault("client_ms", round((perf_counter() - started) * 1000, 3))
                            case["checks"].append({"name": "execution", "passed": False, "error_type": type(exc).__name__})
                            if body is not None:
                                case["performance"] = body.get("performance")
                        case["passed"] = bool(case["checks"]) and all(c["passed"] for c in case["checks"])
                        report["cases"].append(case)
                        print(json.dumps({"case": turn.id, "repeat": repeat + 1, "passed": case["passed"],
                                          "client_ms": case["client_ms"]}), flush=True)
            report["summary"] = summarize(report["cases"])
            report["passed"] = bool(report["cases"]) and all(case["passed"] for case in report["cases"])
            if compare:
                report["comparison"] = compare_reports(report, json.loads(compare.read_text(encoding="utf-8")))
    except Exception as exc:
        report["passed"] = False
        report["error_type"] = type(exc).__name__
        raise
    finally:
        try:
            if mode == "live":
                cleanup_verification_rows(users, keys)
            report["test_rows_cleaned"] = True  # Offline fixture store dies with this run.
        finally:
            settings.LLM_API_KEY = previous_key
            report.setdefault("summary", summarize(report["cases"]))
            report["metadata"]["finished_at"] = datetime.now(timezone.utc).isoformat()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            write_readable_report(report, output)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["offline", "live"], default="offline")
    parser.add_argument("--suite", type=Path, default=ROOT / "data/evaluation/fixed_cases.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--scenario", action="append")
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    output = args.output or ROOT / f"docs/fixed_evaluation_{args.mode}.json"
    report = evaluate(args.suite, args.mode, output, repeats=args.repeats,
                      scenario_ids=args.scenario, compare=args.compare)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
