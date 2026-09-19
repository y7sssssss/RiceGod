from typing import Any
from itertools import islice
from threading import Lock

from neo4j import GraphDatabase, Query, READ_ACCESS, WRITE_ACCESS

from ricegod.config import settings


class Neo4jClient:
    """Small Neo4j driver wrapper for read/write Cypher execution."""

    def __init__(
        self,
        uri: str | None = None,
        user: str | None = None,
        password: str | None = None,
        database: str | None = None,
    ) -> None:
        self.uri = uri or settings.NEO4J_URI
        self.user = user or settings.NEO4J_USER
        self.password = password if password is not None else settings.NEO4J_PASSWORD
        self.database = database or settings.NEO4J_DATABASE
        self._driver = None
        self._driver_lock = Lock()

    def _get_driver(self):
        if not self.password:
            raise RuntimeError("NEO4J_PASSWORD is not configured.")
        with self._driver_lock:
            if self._driver is None:
                self._driver = GraphDatabase.driver(
                    self.uri,
                    auth=(self.user, self.password),
                    connection_timeout=5.0,
                )
        return self._driver

    def run_query(
        self,
        cypher: str,
        params: dict[str, Any] | None = None,
        *,
        max_records: int | None = None,
        read_only: bool = False,
        timeout: float | None = None,
    ) -> list[dict[str, Any]]:
        """Run Cypher and return rows as dictionaries."""
        driver = self._get_driver()
        with driver.session(database=self.database, default_access_mode=READ_ACCESS if read_only else WRITE_ACCESS) as session:
            result = session.run(Query(cypher, timeout=timeout), params or {})
            records = islice(result, max_records) if max_records is not None else result
            return [record.data() for record in records]

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None
