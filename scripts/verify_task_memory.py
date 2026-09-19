"""Opt-in real-model/database checks for task scopes and memory acknowledgements.

The last case deliberately rejects memory INSERT/UPDATE calls for one synthetic
user. All other retrieval/model/storage calls are real. Only UUID test rows are
cleaned. Run: python -m scripts.verify_task_memory
"""

import json
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
    user = "task-memory-verification-" + uuid4().hex
    users = [user, user + "-local"]
    report = {"verified_at": datetime.now(timezone.utc).isoformat(), "checks": [], "chats": [],
              "scope": __doc__, "test_rows_cleaned": False}

    def check(name, passed):
        report["checks"].append({"name": name, "passed": bool(passed)})
        print(json.dumps(report["checks"][-1], ensure_ascii=False), flush=True)

    runtime = None
    thread_keys = set()
    try:
        with tracing_context(enabled=False), TestClient(api.app) as client:
            runtime = api.app.state.runtime
            if not runtime.memory_store or not runtime.memory_service:
                raise RuntimeError("Memory store unavailable")

            def chat(name, message, user_id=user):
                thread_keys.add(_thread_key(user_id, name))
                response = client.post("/chat", json={"user_id": user_id,
                    "conversation_id": name, "message": message})
                response.raise_for_status()
                body = response.json()
                snapshot = runtime.graph.get_state({"configurable": {
                    "thread_id": _thread_key(user_id, name)}}).values
                records = runtime.memory_store.get_messages(name, 200, user_id=user_id)
                check(name + "_history_matches_response", records[-1].content == body["answer"]
                      == snapshot["messages"][-1].content == snapshot["answer"])
                report["chats"].append({"name": name, "question": message, "response": body,
                    "plan": snapshot["plan"].model_dump(),
                    "task_results": [result.model_dump() for result in snapshot["task_results"].values()]})
                return body, snapshot["plan"], snapshot["task_results"]

            body, _, _ = chat("confirmed_save", "这是自动化测试虚构用户，不含真实个人信息。请长期记住：我不吃香菜，也不能吃辣。")
            saved = body["memory_write"]["saved"]
            stored_ids = {record.id for record in runtime.memory_store.list_memories(user)}
            check("successful_save_has_database_receipt", bool(saved)
                  and all(record["id"] in stored_ids for record in saved)
                  and "已保存长期记忆" in body["answer"])

            body, plan, results = chat("independent_counts", "早餐用鸡蛋推荐两道菜，晚餐用土豆推荐一道菜。")
            tasks = [task for task in plan.tasks if task.route == "recipe_query"]
            check("two_independent_recipe_tasks", len(tasks) == 2)
            check("ingredients_and_counts_do_not_leak", len(tasks) == 2
                  and tasks[0].constraints.required_ingredients == ["鸡蛋"]
                  and tasks[1].constraints.required_ingredients == ["土豆"]
                  and [task.recipe_request.limit for task in tasks] == [2, 1])
            check("shared_memory_restrictions", len(tasks) == 2 and all(
                "香菜" in task.constraints.excluded_ingredients and task.constraints.avoid_spicy for task in tasks))
            check("actual_neo4j_results_match_each_task", len(tasks) == 2 and all(
                results[task.id].status == "ok" and len(results[task.id].records) == task.recipe_request.limit
                and all(task.constraints.matches(row) for row in results[task.id].records) for task in tasks))
            check("all_selected_recipes_shown", len(tasks) == 2 and all(
                display_name(row) in body["answer"] for task in tasks for row in results[task.id].records))

            _, plan, results = chat("independent_local_restrictions",
                "早餐用鸡蛋不要土豆不吃辣推荐一道菜，晚餐用土豆做麻辣菜推荐两道菜。", users[1])
            tasks = [task for task in plan.tasks if task.route == "recipe_query"]
            check("local_exclusion_and_spicy_requirement_remain_independent", len(tasks) == 2
                  and tasks[0].constraints.excluded_ingredients == ["土豆"] and tasks[0].constraints.avoid_spicy
                  and not tasks[1].constraints.excluded_ingredients and not tasks[1].constraints.avoid_spicy
                  and "麻辣" in tasks[1].constraints.flavors)
            check("local_constraints_applied_in_real_graph", len(tasks) == 2 and all(
                results[task.id].status == "ok" and results[task.id].records
                and all(task.constraints.matches(row) for row in results[task.id].records) for task in tasks))

            original_add, original_update = runtime.memory_store.add_memory, runtime.memory_store.update_memory
            rejected = []
            def reject_or_delegate(operation, **kwargs):
                if kwargs.get("user_id") == user:
                    rejected.append(operation)
                    raise RuntimeError("Deliberate synthetic-user write failure")
                return (original_add if operation == "add" else original_update)(**kwargs)
            runtime.memory_store.add_memory = lambda **kw: reject_or_delegate("add", **kw)
            runtime.memory_store.update_memory = lambda **kw: reject_or_delegate("update", **kw)
            try:
                body, _, _ = chat("failed_save", "这是自动化测试虚构偏好，请长期记住：我喜欢蒸菜。")
                check("controlled_write_failure_exercised", bool(rejected))
                check("failed_write_does_not_claim_success", body["memory_write"]["status"] == "failed"
                      and not body["memory_write"]["saved"] and "已保存长期记忆" not in body["answer"]
                      and "已记住" not in body["answer"] and "未能保存" in body["answer"])
            finally:
                runtime.memory_store.add_memory, runtime.memory_store.update_memory = original_add, original_update
    except Exception as exc:
        check("verification_completed_" + type(exc).__name__, False)
    finally:
        if runtime and runtime.memory_store:
            cleanup_verification_rows(users, thread_keys)
            report["test_rows_cleaned"] = True
        report["passed"] = bool(report["checks"]) and all(item["passed"] for item in report["checks"])
        Path("docs/task_memory_verification.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        print(json.dumps({"passed": report["passed"], "test_rows_cleaned": report["test_rows_cleaned"]}), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
