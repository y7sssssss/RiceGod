"""Verify performance fields over TCP against a running local RiceGod service."""

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from ricegod.application.chat.service import _thread_key
from ricegod.infrastructure.checkpoint_store import open_checkpointer
from ricegod.infrastructure.memory_store import MemoryStore
from scripts.verification_cleanup import cleanup_verification_rows


def main():
    user = "performance-http-" + uuid4().hex
    key = _thread_key(user, "verify")
    cfg = {"configurable": {"thread_id": key}}
    report = {"verified_at": datetime.now(timezone.utc).isoformat(), "checks": [], "responses": [],
              "test_rows_cleaned": False}

    def check(name, passed):
        report["checks"].append({"name": name, "passed": bool(passed)})
        print(json.dumps(report["checks"][-1]), flush=True)
        if not passed:
            raise AssertionError(name)

    try:
        with httpx.Client(base_url="http://127.0.0.1:8000", timeout=180) as client:
            client.get("/health").raise_for_status()
            for index, message in enumerate(("用鸡蛋推荐两道家常菜", "第二道怎么做")):
                response = client.post("/chat", json={"user_id": user, "conversation_id": "verify", "message": message})
                response.raise_for_status()
                body = response.json()
                report["responses"].append(body)
                performance = body["performance"]
                check(f"timing_{index}", performance["total_ms"] > 0 and
                      {"total", "graph", "router", "planner", "recipe_retrieval", "answer"}
                      <= {stage["name"] for stage in performance["stages"]})
                check(f"nonnegative_intervals_{index}", all(stage["duration_ms"] >= 0 and stage["start_ms"] >= 0
                                                            for stage in performance["stages"]))
                check(f"no_user_data_in_metrics_{index}", user not in json.dumps(performance) and message not in json.dumps(performance))
                with open_checkpointer() as saver:
                    state = saver.get_tuple(cfg).checkpoint["channel_values"]
                    check(f"metrics_not_persisted_{index}", "performance" not in state)
                    check(f"history_matches_{index}", state["answer"] == state["messages"][-1].content == body["answer"]
                          == MemoryStore().get_messages("verify", user_id=user)[-1].content)
                    if index == 0:
                        names = [row.get("菜名") or row.get("name") for row in state["last_recommendations"][0].records]
                        check("two_actual_recipes", len(names) == 2 and all(name in body["answer"] for name in names))
                    else:
                        check("selection_preserved", state["plan"].selected_recipe_name == names[1]
                              and body["sources"] == ["neo4j:Dish:" + names[1]])
                        check("distinct_trace_ids", performance["trace_id"] != report["responses"][0]["performance"]["trace_id"])
        report["passed"] = True
    finally:
        cleanup_verification_rows([user], [key])
        report["test_rows_cleaned"] = True
        Path("docs/performance_http_verification.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
