"""Remove only explicitly scoped synthetic verification users and threads."""

from collections.abc import Iterable

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver

from ricegod.infrastructure.checkpoint_store import postgres_dsn


def cleanup_verification_rows(users: Iterable[str], thread_keys: Iterable[str]) -> None:
    users, thread_keys = list(set(users)), list(set(thread_keys))
    if not users:
        return
    with psycopg.connect(postgres_dsn(), connect_timeout=5) as conn:
        for table in ("memories", "messages"):
            conn.execute(f"DELETE FROM {table} WHERE user_id = ANY(%s)", (users,))
        if thread_keys and conn.execute("SELECT to_regclass('checkpoints')").fetchone()[0]:
            saver = PostgresSaver(conn)
            for key in thread_keys:
                saver.delete_thread(key)
