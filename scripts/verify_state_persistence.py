"""Live TCP verification in two phases separated by an actual API restart.

Run --phase before, restart the API, then run --phase after. The manifest only
contains synthetic user data. The after/cleanup phases delete only those rows.
"""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from ricegod.application.chat.service import _thread_key
from ricegod.application.agents.state import RecommendationGroup
from ricegod.infrastructure.checkpoint_store import open_checkpointer
from scripts.verification_cleanup import cleanup_verification_rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True, choices=["before", "after", "cleanup"])
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--manifest", default=".codex_tmp/state-persistence-live.json")
    parser.add_argument("--server-pid", type=int)
    args = parser.parse_args()
    path = Path(args.manifest)
    if args.phase == "before":
        if path.exists() and not json.loads(path.read_text(encoding="utf-8")).get("test_rows_cleaned"):
            raise RuntimeError("Existing manifest: run --phase cleanup before another verification")
        report = {"verified_at": datetime.now(timezone.utc).isoformat(),
                  "user": "state-live-" + uuid4().hex, "conversation": "restart",
                  "checks": [], "chats": [], "server_pids": [args.server_pid],
                  "test_rows_cleaned": False}
    else:
        report = json.loads(path.read_text(encoding="utf-8"))
    user, conversation = report["user"], report["conversation"]
    keys = [_thread_key(user, conversation), _thread_key(user + "-other", conversation),
            _thread_key(user, conversation + "-other")]

    def save():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def check(name, passed):
        item = {"name": name, "passed": bool(passed)}
        report["checks"].append(item)
        print(json.dumps(item), flush=True)
        save()
        if not passed:
            raise AssertionError(name)

    def cleanup():
        cleanup_verification_rows([user, user + "-other"], keys)
        report["test_rows_cleaned"] = True
        save()

    if args.phase == "cleanup":
        cleanup()
        return 0
    save()
    completed = False
    try:
        with httpx.Client(base_url=args.url, timeout=120, trust_env=False) as client, open_checkpointer() as saver:
            client.get("/health").raise_for_status()
            cfg = {"configurable": {"thread_id": keys[0]}}

            def state(config=cfg):
                row = saver.get_tuple(config)
                return row.checkpoint["channel_values"] if row else {}

            def identity(values):
                return [[row.get("菜名") or row.get("name") for row in group.records]
                        for group in values.get("last_recommendations", [])]

            def chat(name, message):
                started = time.perf_counter()
                response = client.post("/chat", json={"user_id": user,
                    "conversation_id": conversation, "message": message})
                response.raise_for_status()
                body, values = response.json(), state()
                history = client.get("/messages", params={"user_id": user,
                    "conversation_id": conversation}).json()
                report["chats"].append({"name": name, "question": message, "response": body,
                    "elapsed_seconds": round(time.perf_counter() - started, 2)})
                check(name + "_answer_and_history_match", body["answer"] == values["answer"] == history[-1]["content"])
                return body, values

            if args.phase == "before":
                body, _ = chat("save_preference", "请记住，我喜欢清淡口味。")
                check("memory_save_confirmed", body["memory_write"]["status"] == "saved")
                _, values = chat("recommend", "用鸡蛋推荐三道不辣的家常菜")
                report["original_names"] = identity(values)
                report["original_message_ids"] = [msg.id for msg in values["messages"]]
                check("three_structured_recommendations", len(report["original_names"]) == 1
                      and len(report["original_names"][0]) == 3
                      and all(isinstance(group, RecommendationGroup) for group in values["last_recommendations"]))
                report["before_complete"] = True
                save()
            else:
                report["server_pids"].append(args.server_pid)
                check("before_phase_complete", report.get("before_complete", False))
                check("different_api_process", all(report["server_pids"])
                      and report["server_pids"][0] != report["server_pids"][1])
                values = state()
                check("recommendations_restored_before_new_turn", identity(values) == report["original_names"])
                check("message_ids_and_order_restored", [msg.id for msg in values["messages"]]
                      == report["original_message_ids"])
                check("typed_groups_restored", all(isinstance(group, RecommendationGroup)
                      for group in values["last_recommendations"]))
                for label, message, index in [("thanks", "谢谢", None),
                    ("second", "第二道菜怎么做", 1), ("third", "第三道菜怎么做", 2)]:
                    body, values = chat(label, message)
                    check(label + "_preserves_original_list", identity(values) == report["original_names"])
                    if index is not None:
                        expected = report["original_names"][0][index]
                        check(label + "_selects_original_recipe", body["sources"] == ["neo4j:Dish:" + expected])
                check("no_duplicate_messages_after_restore", len(values["messages"])
                      == len(report["original_message_ids"]) + 6)
                check("user_and_conversation_isolation", all(not state({"configurable": {"thread_id": key}})
                      for key in keys[1:]))
            completed = True
    finally:
        if args.phase == "after":
            report["passed"] = completed and all(item["passed"] for item in report["checks"])
            cleanup()
            output = Path("docs/state_persistence_verification.json")
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
