"""Live model/DB checks with explicit synthetic long-history fixtures."""

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from langsmith import tracing_context

from ricegod.application.agents import answer, conversation, planner
from ricegod.application.agents.context import estimate_tokens
from ricegod.application.chat.service import _thread_key
from ricegod.main import create_app
from ricegod.bootstrap import create_runtime
from scripts.verification_cleanup import cleanup_verification_rows


def main():
    user = "context-verification-" + uuid4().hex
    key = _thread_key(user, "long")
    cfg = {"configurable": {"thread_id": key}}
    report = {"verified_at": datetime.now(timezone.utc).isoformat(), "checks": [],
              "contexts": [], "chats": [], "fixture_history_turns": 30,
              "test_rows_cleaned": False}
    captured = []
    replacement_runtime = None
    originals = {module: module.build_model_context for module in (planner, conversation, answer)}

    def wrap(original):
        def build(state, purpose, **kwargs):
            messages = original(state, purpose, **kwargs)
            captured.append((purpose, messages, len(state.messages)))
            report["contexts"].append({"purpose": purpose, "checkpoint_messages": len(state.messages),
                "model_messages": len(messages), "estimated_input_tokens": estimate_tokens(messages),
                "historical_assistants": sum(isinstance(m, AIMessage) for m in messages)})
            return messages
        return build

    def check(name, passed):
        report["checks"].append({"name": name, "passed": bool(passed)})
        print(json.dumps(report["checks"][-1]), flush=True)
        if not passed:
            raise AssertionError(name)

    def chat(client, app, message):
        response = client.post("/chat", json={"user_id": user, "conversation_id": "long", "message": message})
        response.raise_for_status()
        body = response.json()
        report["chats"].append({"question": message, "response": body})
        runtime = app.state.runtime
        state = runtime.graph.get_state(cfg).values
        check("history_matches_" + str(len(report["chats"])), body["answer"] == state["answer"]
              == runtime.memory_store.get_messages("long", user_id=user)[-1].content)
        return body, state

    try:
        for module, original in originals.items():
            module.build_model_context = wrap(original)
        with tracing_context(enabled=False):
            app = create_app()
            with TestClient(app) as client:
                runtime = app.state.runtime
                source = runtime.memory_store.add_message("long", user, "user", "喜欢清淡口味")
                runtime.memory_store.add_memory(user_id=user, type_="dietary_preference", content="喜欢清淡口味",
                    embedding=runtime.memory_service.embedder.embed_query("喜欢清淡口味"),
                    source_message_id=source.id, evidence="喜欢清淡口味")
                messages = []
                # Fixtures are explicit, not thirty real model-generated chat turns.
                for i in range(30):
                    for role, content in [("user", f"历史寒暄{i}，谢谢"), ("assistant", f"历史回复{i}，不客气")]:
                        runtime.memory_store.add_message("long", user, role, content)
                        messages.append((HumanMessage if role == "user" else AIMessage)(content=content))
                runtime.graph.update_state(cfg, {"messages": messages}, as_node="synthesize_answer")
                body, state = chat(client, app, "用鸡蛋推荐三道家常菜")
                names = [row.get("菜名") or row.get("name") for row in state["last_recommendations"][0].records]
                check("three_actual_recommendations", len(names) == 3 and all(n in body["answer"] for n in names))
                check("old_history_excluded", all("历史寒暄0，" not in str(msgs) for _, msgs, _ in captured))
                check("window_bounded_all_nodes", all(sum(isinstance(m, AIMessage) for m in msgs) <= 6
                                                       for _, msgs, _ in captured))
                check("full_checkpoint_history", len(state["messages"]) == 62)
                count = len(captured)
                body, state = chat(client, app, "把清淡口味改成喜欢麻辣口味")
                marker = state["context_history_after"]
                check("correction_does_not_call_dialogue_models", len(captured) == count
                      and body["memory_write"]["status"] == "updated")
                # Recreate pool/graph/service on the same HTTP event loop.
                # This avoids SDK clients cached across separate TestClient loops.
                runtime.close()
                replacement_runtime = create_runtime()
                app.state.runtime = replacement_runtime
                count = len(captured)
                body, state = chat(client, app, "第二道怎么做")
                check("selection_survives_runtime_restart", state["plan"].selected_recipe_name == names[1]
                      and body["sources"] == ["neo4j:Dish:" + names[1]])
                check("history_boundary_persisted", state["context_history_after"] == marker
                      and len(state["messages"]) == 66)
                check("stale_preference_absent_from_model_input", all("清淡" not in str(msgs)
                      for _, msgs, _ in captured[count:]))
                check("current_memory_present", all("喜欢麻辣口味" in str(msgs)
                      for _, msgs, _ in captured[count:]))
                count = len(captured)
                body, state = chat(client, app, "忘掉麻辣口味偏好")
                check("forget_does_not_call_dialogue_models", len(captured) == count
                      and body["memory_write"]["status"] == "deleted")
                count = len(captured)
                body, state = chat(client, app, "你好")
                check("forgotten_preference_absent", all("麻辣口味" not in str(msgs)
                      for _, msgs, _ in captured[count:]) and not body["used_memories"])
                check("complete_history_still_available", len(state["messages"]) == 70
                      and "把清淡口味改成喜欢麻辣口味" in str(state["messages"]))
            report["passed"] = True
    finally:
        if replacement_runtime is not None:
            replacement_runtime.close()
        for module, original in originals.items():
            module.build_model_context = original
        cleanup_verification_rows([user], [key])
        report["test_rows_cleaned"] = True
        Path("docs/context_management_verification.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
