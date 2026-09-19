"""Opt-in live API checks; requires databases and makes paid model calls.

Run with: python -m scripts.verify_end_to_end --output docs/end_to_end_verification.json
Uses unique test users and deletes only their PostgreSQL rows on completion.
"""

import argparse
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
from ricegod.domain.recipes import RecipeConstraints
from ricegod.config import settings
from ricegod.application.memory.provenance import find_user_evidence, normalize_text
from ricegod.domain.recipe_ranking import display_name


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("docs/end_to_end_verification.json"))
    args = parser.parse_args()
    user = "live-verification-" + uuid4().hex
    other_user = user + "-other"
    report = {"verified_at": datetime.now(timezone.utc).isoformat(),
              "scope": "Real FastAPI handlers through TestClient, real LLM, Embedding and databases; no mocked retrieval. Optional LangSmith upload is disabled during verification.",
              "configuration": {"llm_model": settings.LLM_MODEL,
                                "embedding_model": settings.EMBEDDING_MODEL,
                                "embedding_dimension": settings.EMBEDDING_DIMENSION,
                                "milvus_collection": settings.MILVUS_COLLECTION},
              "checks": [], "chats": [], "test_rows_cleaned": False}

    def check(name, passed, **details):
        report["checks"].append({"name": name, "passed": bool(passed), **details})
        print(json.dumps(report["checks"][-1], ensure_ascii=False), flush=True)

    runtime = None
    thread_keys = set()
    try:
        with tracing_context(enabled=False), TestClient(api.app) as client:
            runtime = api.app.state.runtime
            check("postgres_startup", runtime.memory_store is not None and runtime.memory_service is not None)
            if not runtime.memory_store or not runtime.memory_service:
                raise RuntimeError("Memory service unavailable")
            count = runtime.recipe_graph_repository.client.run_query("MATCH (d:Dish) RETURN count(d) AS count")[0]["count"]
            check("neo4j_seed_data", count > 0, dish_count=count)

            def chat(name, conversation, message):
                thread_keys.add(_thread_key(user, conversation))
                started = time.perf_counter()
                response = client.post("/chat", json={"user_id": user,
                    "conversation_id": conversation, "message": message})
                check(name + "_http", response.status_code == 200, status_code=response.status_code)
                response.raise_for_status()
                body = response.json()
                check(name + "_route", bool(body["route"]))
                snapshot = runtime.graph.get_state({"configurable": {"thread_id": _thread_key(user, conversation)}}).values
                plan = snapshot["plan"]
                results = snapshot["task_results"]
                saved = client.get("/memories", params={"user_id": user}).json()
                with runtime.memory_store._connect() as connection:
                    user_messages = connection.execute(
                        "SELECT id, role, content FROM messages WHERE user_id = %s", (user,)
                    ).fetchall()
                check(name + "_memory_evidence", all(
                    (quote := find_user_evidence(user_messages, m["source_message_id"], m["evidence"]))
                    is not None and normalize_text(m["content"]) == quote for m in saved))
                if name in {"cross_session_recipe", "culture_retrieval", "recipe_then_culture"}:
                    check(name + "_no_invented_flavor", not any("葱香" in m["content"] for m in saved))
                    check(name + "_restrictions_preserved", any("香菜" in m["content"] for m in saved)
                          and any("辣" in m["content"] for m in saved))
                report["chats"].append({"name": name, "question": message,
                    "elapsed_seconds": round(time.perf_counter() - started, 2),
                    "answer": body["answer"], "route": body["route"], "sources": body["sources"],
                    "used_memories": [{"type": m["type"], "content": m["content"]} for m in body["used_memories"]],
                    "saved_memories": [{key: m[key] for key in
                        ("id", "type", "content", "source_message_id", "evidence")} for m in saved],
                    "plan": plan.model_dump(), "task_results": [r.model_dump() for r in results.values()]})
                print(json.dumps({"completed": name, "elapsed_seconds": report["chats"][-1]["elapsed_seconds"],
                    "route": body["route"], "sources": body["sources"]}, ensure_ascii=False), flush=True)
                return body, plan, results

            chat("remember_preferences", "remember", "这是自动化测试中虚构用户的对话，不含真实个人信息。请长期记住我的饮食习惯：我不吃香菜，也不能吃辣。")
            memories = client.get("/memories", params={"user_id": user}).json()
            check("memory_persisted", any("香菜" in m["content"] for m in memories)
                  and any("辣" in m["content"] for m in memories),
                  memories=[{"type": m["type"], "content": m["content"]} for m in memories])
            check("memory_types_preserved", any(m["type"] == "disliked_ingredient" and "香菜" in m["content"] for m in memories)
                  and any(m["type"] == "dietary_preference" and "辣" in m["content"] for m in memories))
            check("memory_facts_not_combined", all("辣" not in m["content"] for m in memories
                  if m["type"] == "disliked_ingredient" and "香菜" in m["content"])
                  and all("香菜" not in m["content"] for m in memories
                  if m["type"] == "dietary_preference" and "辣" in m["content"]))

            body, plan, results = chat("cross_session_recipe", "new-session", "用鸡蛋推荐一道家常菜，给出材料和步骤。")
            check("cross_session_recall", any("香菜" in m["content"] for m in body["used_memories"]))
            check("memory_applied_to_filters", "香菜" in plan.constraints.excluded_ingredients and plan.constraints.avoid_spicy)
            recipe_results = [r for r in results.values() if r.route == "recipe_query"]
            expected = RecipeConstraints(required_ingredients=["鸡蛋"], excluded_ingredients=["香菜"], avoid_spicy=True)
            check("real_neo4j_constrained_recipe", bool(recipe_results) and all(
                r.status == "ok" and r.records and all(expected.matches(row) for row in r.records)
                and all(s.startswith("neo4j:Dish:") for s in r.sources) for r in recipe_results))

            body, _, results = chat("culture_retrieval", "culture", "辣椒是什么时候传入中国的？请根据知识库回答。")
            knowledge_results = [r for r in results.values() if r.route == "food_knowledge"]
            check("real_milvus_culture", bool(knowledge_results) and all(
                r.status == "ok" and r.records and any(s.startswith("data.txt#") for s in r.sources)
                for r in knowledge_results))

            _, plan, results = chat("recipe_then_culture", "combined", "推荐一道含鸡蛋的家常菜，并介绍这道菜的历史来历。")
            dependent_tasks = [t for t in plan.tasks if t.route == "food_knowledge" and t.depends_on]
            check("dependent_culture_uses_retrieved_dish", bool(dependent_tasks) and all(
                any(str(row.get("菜名") or row.get("name")) in results[t.id].question
                    for dep in t.depends_on for row in results[dep].records)
                for t in dependent_tasks))

            chat("explicit_new_flavor", "explicit-flavor", "这是同一虚构测试用户的偏好：请长期记住，我喜欢葱香口味。")
            final_memories = client.get("/memories", params={"user_id": user}).json()
            check("explicit_flavor_can_be_saved", any("喜欢葱香" in m["content"] for m in final_memories))
            check("independent_restrictions_retained", any("香菜" in m["content"] for m in final_memories)
                  and any("不能吃辣" in m["content"] for m in final_memories))

            body, plan, results = chat("ranked_default_three", "ranked", "用鸡蛋推荐家常菜，给我几个选择。")
            ranked = [row for result in results.values() if result.route == "recipe_query" for row in result.records]
            check("ranking_default_three", plan.recipe_request.limit == 3 and len(ranked) == 3)
            check("ranking_reasons_and_restrictions", bool(ranked) and all(row.get("推荐理由")
                and expected.matches(row) for row in ranked))
            check("ranking_three_shown_in_answer", bool(ranked) and all(display_name(row) in body["answer"] for row in ranked))

            _, plan, results = chat("selected_second_recipe", "ranked", "第二道菜怎么做？给出材料和步骤。")
            selected = [row for result in results.values() if result.route == "recipe_query" for row in result.records]
            check("selection_uses_previous_second_recipe", len(selected) == 1 and len(ranked) >= 2
                  and selected[0].get("菜名") == ranked[1].get("菜名") and plan.recipe_request.detailed)

            body, plan, results = chat("ranked_explicit_five", "ranked-five", "用鸡蛋推荐五道家常菜。")
            five = [row for result in results.values() if result.route == "recipe_query" for row in result.records]
            check("ranking_explicit_five", plan.recipe_request.limit == 5 and len(five) == 5)
            check("ranking_five_shown_in_answer", bool(five) and all(display_name(row) in body["answer"] for row in five))

            foreign_messages = client.get("/messages", params={"user_id": other_user, "conversation_id": "remember"}).json()
            foreign_memories = client.get("/memories", params={"user_id": other_user}).json()
            check("user_isolation", foreign_messages == [] and foreign_memories == [])
            if memories:
                deleted = client.delete(f"/memories/{memories[0]['id']}", params={"user_id": other_user}).json()
                check("foreign_delete_rejected", deleted.get("deleted") is False)
    except Exception as exc:
        # Avoid putting credentials or provider response bodies in the shared report.
        check("verification_completed", False, exception_type=type(exc).__name__,
              status_code=getattr(exc, "status_code", None), error_code=getattr(exc, "code", None))
    finally:
        if runtime and runtime.memory_store:
            cleanup_verification_rows([user, other_user], thread_keys)
            report["test_rows_cleaned"] = True
        report["passed"] = bool(report["checks"]) and all(c["passed"] for c in report["checks"])
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        print(json.dumps({"passed": report["passed"], "report": str(args.output),
                          "test_rows_cleaned": report["test_rows_cleaned"]}, ensure_ascii=False), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
