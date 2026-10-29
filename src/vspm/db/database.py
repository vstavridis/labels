"""SQLite connection handling and migrations for local application state."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path) if str(path) != ":memory:" else path
        if isinstance(self.path, Path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._memory_conn: sqlite3.Connection | None = None
        self.migrate()

    def _new_conn(self) -> sqlite3.Connection:
        if self.path == ":memory:":
            if self._memory_conn is None:
                self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
                self._memory_conn.row_factory = sqlite3.Row
            return self._memory_conn
        conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = self._new_conn()
        try:
            yield conn
        finally:
            if self.path != ":memory:":
                conn.close()

    @contextmanager
    def transaction(self, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        """Explicit transaction. IMMEDIATE takes the write lock up front."""
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    def migrate(self) -> list[str]:
        applied: list[str] = []
        with self.connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
            for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if f.stem in done:
                    continue
                conn.execute("BEGIN IMMEDIATE")
                try:
                    for stmt in _split_sql(f.read_text(encoding="utf-8")):
                        conn.execute(stmt)
                    conn.execute("INSERT INTO schema_migrations VALUES (?, ?)", (f.stem, utcnow()))
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
                applied.append(f.stem)
        return applied

    # ---- small helpers ----------------------------------------------------
    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute(sql, params))

    def one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: tuple | dict = ()) -> int:
        with self.transaction() as conn:
            cur = conn.execute(sql, params)
            return cur.lastrowid

    def audit(self, entity: str, entity_id: Any, action: str, details: dict | None = None,
              actor: str | None = None, computer: str | None = None,
              conn: sqlite3.Connection | None = None) -> None:
        params = (utcnow(), actor, computer, entity, str(entity_id), action,
                  json.dumps(details or {}, ensure_ascii=False, default=str))
        sql = ("INSERT INTO audit_log (ts, actor, computer, entity, entity_id, action, details_json) "
               "VALUES (?,?,?,?,?,?,?)")
        if conn is not None:
            conn.execute(sql, params)
        else:
            self.execute(sql, params)

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        r = self.one("SELECT value FROM app_settings WHERE key=?", (key,))
        return r["value"] if r else default

    def set_setting(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO app_settings(key, value, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value, utcnow()),
        )


def _split_sql(script: str) -> list[str]:
    lines = [ln for ln in script.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]
