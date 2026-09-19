"""Opt-in live model/database checks; only UUID fixture rows are cleaned."""

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from langsmith import tracing_context

from ricegod.main import create_app
from ricegod.application.chat.service import _thread_key
from scripts.verification_cleanup import cleanup_verification_rows


def main() -> int:
    users = ["memory-operations-" + uuid4().hex, "memory-operations-other-" + uuid4().hex]
    user, other = users
    keys = set()
    report = {"verified_at": datetime.now(timezone.utc).isoformat(), "checks": [], "chats": [],
              "extraction_model_calls": 0, "test_rows_cleaned": False}
    app = create_app()

    def check(name, passed):
        report["checks"].append({"name": name, "passed": bool(passed)})
        print(json.dumps(report["checks"][-1]), flush=True)
        if not passed:
            raise AssertionError(name)

    try:
        with tracing_context(enabled=False), TestClient(app) as client:
            runtime = app.state.runtime
            store, memories = runtime.memory_store, runtime.memory_service
            if not store or not memories:
                raise RuntimeError("Memory service unavailable")
            original_build = memories.extractor._build_model

            def counted_model():
                report["extraction_model_calls"] += 1
                return original_build()

            memories.extractor._build_model = counted_model

            def chat(name, message, conversation="normal"):
                key = _thread_key(user, conversation)
                keys.add(key)
                response = client.post("/chat", json={"user_id": user,
                    "conversation_id": conversation, "message": message})
                response.raise_for_status()
                body = response.json()
                state = runtime.graph.get_state({"configurable": {"thread_id": key}}).values
                report["chats"].append({"name": name, "question": message, "response": body,
                                      "extraction_model_calls": report["extraction_model_calls"]})
                check(name + "_history_matches", body["answer"] == state["answer"]
                    == store.get_messages(conversation, user_id=user)[-1].content)
                return body, state

            def seed(content, conversation, user_id=user):
                # Explicit fixtures make ambiguity/scope checks independent of
                # model deduplication. Embeddings and SQL are still real calls.
                row = store.add_message(conversation, user_id, "user", content)
                return store.add_memory(user_id=user_id, type_="dietary_preference", content=content,
                    embedding=memories.embedder.embed_query(content), source_message_id=row.id, evidence=content)

            body, _ = chat("save", "请记住，我喜欢清淡口味。")
            check("normal_save_uses_extractor", report["extraction_model_calls"] == 1
                  and body["memory_write"]["status"] == "saved")
            old = body["memory_write"]["saved"][0]
            _, state = chat("recommend", "用鸡蛋推荐三道家常菜")
            original_names = [row.get("菜名") or row.get("name") for row in state["last_recommendations"][0].records]
            check("three_recommendations", len(original_names) == 3)
            count = report["extraction_model_calls"]
            body, _ = chat("thanks", "谢谢！")
            check("thanks_skips_extractor", report["extraction_model_calls"] == count
                  and body["memory_write"]["extraction_skip_reason"] == "pure_greeting")
            body, _ = chat("second", "第二道怎么做")
            check("selection_skips_extractor", report["extraction_model_calls"] == count
                  and body["sources"] == ["neo4j:Dish:" + original_names[1]])
            chat("mixed_feedback", "第二道很好吃，以后多推荐这种")
            check("mixed_feedback_still_uses_extractor", report["extraction_model_calls"] == count + 1)
            count = report["extraction_model_calls"]
            body, _ = chat("correct", "把" + old["content"] + "改成我喜欢麻辣口味")
            check("correction_is_same_row_and_immediate", body["memory_write"]["status"] == "updated"
                and any(row["id"] == old["id"] and row["content"] == "我喜欢麻辣口味" for row in body["used_memories"])
                and report["extraction_model_calls"] == count)
            body, _ = chat("forget", "忘掉我喜欢麻辣口味这条记忆")
            check("forget_removes_row_before_recall", body["memory_write"]["deleted_ids"] == [old["id"]]
                and all(row["id"] != old["id"] for row in body["used_memories"])
                and report["extraction_model_calls"] == count)
            seed("喜欢清淡", "ambiguous")
            seed("偏好清淡少油", "ambiguous")
            body, _ = chat("ambiguous", "忘掉清淡偏好", "ambiguous")
            check("ambiguous_target_does_not_delete", body["memory_write"]["status"] == "needs_clarification"
                  and len([row for row in store.list_memories(user) if "清淡" in row.content]) == 2)
            seed("喜欢葱香", "foreign", other)
            body, _ = chat("foreign", "忘掉葱香偏好", "foreign")
            check("foreign_target_cannot_be_deleted", body["memory_write"]["status"] == "not_found"
                  and len(store.list_memories(other)) == 1)
            row = store.add_message("constraint", user, "user", "不吃鸡蛋")
            record = store.add_memory(user_id=user, type_="disliked_ingredient", content="不吃鸡蛋",
                embedding=memories.embedder.embed_query("不吃鸡蛋"), source_message_id=row.id, evidence="不吃鸡蛋")
            body, state = chat("forget_then_recommend", "忘掉不吃鸡蛋的记忆，用鸡蛋推荐两道菜", "constraint")
            check("forgotten_constraint_not_reapplied", body["memory_write"]["deleted_ids"] == [record.id]
                and "鸡蛋" not in state["plan"].constraints.excluded_ingredients
                and len(state["last_recommendations"][0].records) == 2)
            report["passed"] = True
    finally:
        cleanup_verification_rows(users, keys)
        report["test_rows_cleaned"] = True
        Path("docs/memory_operations_verification.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
