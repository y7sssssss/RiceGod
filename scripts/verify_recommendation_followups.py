"""Opt-in live checks: real chat/model/databases, UUID users, cleanup on exit.

Run: python -m scripts.verify_recommendation_followups
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from langsmith import tracing_context

import ricegod.main as api
from scripts.verification_cleanup import cleanup_verification_rows
from ricegod.application.chat.service import _thread_key
from ricegod.domain.recipe_ranking import display_name


def main() -> int:
    user = "recommendation-followups-" + uuid4().hex
    report = {"verified_at": datetime.now(timezone.utc).isoformat(), "scope": __doc__,
              "checks": [], "chats": [], "test_rows_cleaned": False}

    def check(name, passed):
        report["checks"].append({"name": name, "passed": bool(passed)})
        print(json.dumps(report["checks"][-1]), flush=True)

    def identity(groups):
        return [(group.label, [row.get("菜名") or row.get("name") for row in group.records]) for group in groups]

    runtime = None
    thread_keys = set()
    try:
        with tracing_context(enabled=False), TestClient(api.app) as client:
            runtime = api.app.state.runtime
            if not runtime.memory_store:
                raise RuntimeError("PostgreSQL unavailable")

            def chat(name, message, conversation="followups"):
                thread_keys.add(_thread_key(user, conversation))
                started = time.perf_counter()
                response = client.post("/chat", json={"user_id": user, "conversation_id": conversation, "message": message})
                response.raise_for_status()
                body = response.json()
                snapshot = runtime.graph.get_state({"configurable": {
                    "thread_id": _thread_key(user, conversation)}}).values
                history = runtime.memory_store.get_messages(conversation, 200, user_id=user)
                check(name + "_history_matches", body["answer"] == snapshot["answer"] == history[-1].content)
                report["chats"].append({"name": name, "question": message,
                    "elapsed_seconds": round(time.perf_counter() - started, 2), "response": body,
                    "last_recommendations": [group.model_dump() for group in snapshot["last_recommendations"]],
                    "plan": snapshot["plan"].model_dump(),
                    "task_results": [result.model_dump() for result in snapshot["task_results"].values()]})
                print(json.dumps({"completed": name, "elapsed_seconds": report["chats"][-1]["elapsed_seconds"]}), flush=True)
                return body, snapshot

            def selected(name, body, snapshot, expected):
                results = [result for result in snapshot["task_results"].values() if result.route == "recipe_query"]
                rows = [row for result in results for row in result.records]
                check(name, len(rows) == 1 and (rows[0].get("菜名") or rows[0].get("name")) == expected
                      and all(result.status == "ok" for result in results)
                      and body["sources"] == ["neo4j:Dish:" + expected]
                      and display_name(rows[0]) in body["answer"] and not snapshot["plan"].recipe_request.recommendation)

            body, initial = chat("recommend_three", "这是虚构测试用户的请求。用鸡蛋推荐三道家常菜，不要香菜，不吃辣。")
            original = identity(initial["last_recommendations"])
            check("three_ordered_recommendations", len(original) == 1 and len(original[0][1]) == 3
                  and all(display_name(row) in body["answer"] for row in initial["last_recommendations"][0].records))
            _, snapshot = chat("thanks", "谢谢")
            check("thanks_keeps_list", identity(snapshot["last_recommendations"]) == original)
            body, snapshot = chat("select_second", "第二道菜怎么做？给出材料和步骤。")
            selected("second_matches_original_list", body, snapshot, original[0][1][1])
            check("details_keep_list", identity(snapshot["last_recommendations"]) == original)
            body, snapshot = chat("select_third", "再给我第三道菜的材料和步骤。")
            selected("third_matches_original_list", body, snapshot, original[0][1][2])
            _, snapshot = chat("culture", "辣椒是什么时候传入中国的？请根据知识库回答。")
            check("culture_keeps_list", identity(snapshot["last_recommendations"]) == original)
            check("real_culture_retrieval", any(result.route == "food_knowledge" and result.status == "ok"
                  and result.records for result in snapshot["task_results"].values()))
            body, snapshot = chat("after_culture", "第一道菜怎么做？")
            selected("selection_after_culture_matches_original", body, snapshot, original[0][1][0])

            _, snapshot = chat("new_grouped_list", "早餐用鸡蛋推荐两道菜，晚餐用土豆推荐两道菜。")
            grouped = identity(snapshot["last_recommendations"])
            check("new_list_replaces_previous_with_two_groups", [label for label, _ in grouped] == ["早餐", "晚餐"]
                  and [len(names) for _, names in grouped] == [2, 2])
            _, snapshot = chat("grouped_thanks", "谢谢")
            check("grouped_thanks_keeps_list", identity(snapshot["last_recommendations"]) == grouped)
            body, snapshot = chat("ambiguous_group", "第二道菜怎么做？")
            check("ambiguous_group_clarifies", "哪一组" in body["answer"] and not body["sources"]
                  and identity(snapshot["last_recommendations"]) == grouped)
            body, snapshot = chat("dinner_second", "晚餐第二道菜怎么做？")
            selected("dinner_uses_group_local_number", body, snapshot, grouped[1][1][1])
            body, snapshot = chat("breakfast_first", "早餐第一道菜怎么做？")
            selected("breakfast_selection_after_dinner_details", body, snapshot, grouped[0][1][0])
            check("repeated_group_details_keep_list", identity(snapshot["last_recommendations"]) == grouped)
            body, snapshot = chat("out_of_range", "晚餐第十道菜怎么做？")
            check("out_of_range_clarifies", "只有 2 道菜" in body["answer"] and not body["sources"]
                  and identity(snapshot["last_recommendations"]) == grouped)
            body, snapshot = chat("other_conversation", "第二道菜怎么做？", "empty-conversation")
            check("other_conversation_has_no_list", not snapshot["last_recommendations"] and not body["sources"]
                  and "还没有可选择的推荐列表" in body["answer"])
    except Exception as exc:
        check("verification_completed_" + type(exc).__name__, False)
    finally:
        if runtime and runtime.memory_store:
            cleanup_verification_rows([user], thread_keys)
            report["test_rows_cleaned"] = True
        report["passed"] = bool(report["checks"]) and all(item["passed"] for item in report["checks"])
        Path("docs/recommendation_followups_verification.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        print(json.dumps({"passed": report["passed"], "test_rows_cleaned": report["test_rows_cleaned"]}), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
