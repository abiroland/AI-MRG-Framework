"""Database backends for the registry: SQLite (default) and PostgreSQL.

The registry speaks a small, portable subset of SQL with ``?`` placeholders; each backend
translates as needed. Append-only rules for evidence and lifecycle events are enforced by
database triggers in both backends.
"""

from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

Params = Sequence[Any]

_TABLES = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assets (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    data       TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence (
    id          TEXT PRIMARY KEY,
    asset_id    TEXT NOT NULL REFERENCES assets(id),
    seq         INTEGER NOT NULL,
    control_id  TEXT NOT NULL,
    data        TEXT NOT NULL,
    record_hash TEXT NOT NULL,
    UNIQUE (asset_id, seq)
);
CREATE TABLE IF NOT EXISTS findings (
    id       TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES assets(id),
    data     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    asset_id    TEXT NOT NULL REFERENCES assets(id),
    occurred_at TEXT NOT NULL,
    data        TEXT NOT NULL,
    seq         INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS users (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    token_hash TEXT NOT NULL UNIQUE,
    data       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS webhooks (
    id   TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
"""

_SQLITE_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS evidence_no_update BEFORE UPDATE ON evidence
BEGIN SELECT RAISE(ABORT, 'evidence is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evidence_no_delete BEFORE DELETE ON evidence
BEGIN SELECT RAISE(ABORT, 'evidence is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'lifecycle events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'lifecycle events are append-only'); END;
"""

_POSTGRES_TRIGGERS = """
CREATE OR REPLACE FUNCTION aimrg_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END
$$;
CREATE OR REPLACE TRIGGER evidence_append_only BEFORE UPDATE OR DELETE ON evidence
    FOR EACH ROW EXECUTE FUNCTION aimrg_append_only();
CREATE OR REPLACE TRIGGER events_append_only BEFORE UPDATE OR DELETE ON events
    FOR EACH ROW EXECUTE FUNCTION aimrg_append_only();
"""


class BackendError(Exception):
    """A database error, normalized across backends."""


class IntegrityViolation(BackendError):
    """A uniqueness or foreign-key violation."""


class Database(ABC):
    kind: str

    @abstractmethod
    def query(self, sql: str, params: Params = ()) -> list[tuple[Any, ...]]: ...

    @abstractmethod
    def run(self, sql: str, params: Params = ()) -> int:
        """Execute a statement; return the number of affected rows."""

    @abstractmethod
    def script(self, sql: str) -> None:
        """Execute several statements without parameters."""

    @abstractmethod
    @contextmanager
    def transaction(self) -> Iterator[None]: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def create_schema(self) -> None: ...

    def query_one(self, sql: str, params: Params = ()) -> tuple[Any, ...] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None


class SQLiteDatabase(Database):
    kind = "sqlite"

    def __init__(self, path: Path) -> None:
        self.path = path
        # One connection per Registry object; the API opens one per request, which may
        # be used from a worker thread, so same-thread checking is disabled.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._in_tx = False

    def _call(self, sql: str, params: Params) -> sqlite3.Cursor:
        try:
            cursor = self._conn.execute(sql, tuple(params))
        except sqlite3.IntegrityError as exc:
            raise IntegrityViolation(str(exc)) from exc
        except sqlite3.DatabaseError as exc:
            raise BackendError(str(exc)) from exc
        if not self._in_tx and self._conn.in_transaction:
            self._conn.commit()
        return cursor

    def query(self, sql: str, params: Params = ()) -> list[tuple[Any, ...]]:
        return [tuple(r) for r in self._call(sql, params).fetchall()]

    def run(self, sql: str, params: Params = ()) -> int:
        return int(self._call(sql, params).rowcount)

    def script(self, sql: str) -> None:
        try:
            self._conn.executescript(sql)
        except sqlite3.DatabaseError as exc:
            raise BackendError(str(exc)) from exc

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._in_tx:
            yield
            return
        self._in_tx = True
        try:
            self._conn.execute("BEGIN")
            yield
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()
        finally:
            self._in_tx = False

    def close(self) -> None:
        self._conn.close()

    def create_schema(self) -> None:
        self.script(_TABLES + _SQLITE_TRIGGERS)


class PostgresDatabase(Database):
    kind = "postgresql"

    def __init__(self, url: str) -> None:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise BackendError('PostgreSQL support needs: pip install "ai-mrg[postgres]"') from exc
        self._psycopg = psycopg
        try:
            self._conn = psycopg.connect(url, autocommit=True)
        except psycopg.Error as exc:
            raise BackendError(f"cannot connect to PostgreSQL: {exc}") from exc
        self._in_tx = False

    @staticmethod
    def _translate(sql: str) -> str:
        return sql.replace("%", "%%").replace("?", "%s")

    def _call(self, sql: str, params: Params) -> Any:
        psycopg = self._psycopg
        try:
            return self._conn.execute(self._translate(sql), tuple(params))
        except psycopg.errors.IntegrityError as exc:
            raise IntegrityViolation(str(exc)) from exc
        except psycopg.Error as exc:
            raise BackendError(str(exc)) from exc

    def query(self, sql: str, params: Params = ()) -> list[tuple[Any, ...]]:
        return [tuple(r) for r in self._call(sql, params).fetchall()]

    def run(self, sql: str, params: Params = ()) -> int:
        return int(self._call(sql, params).rowcount)

    def script(self, sql: str) -> None:
        try:
            self._conn.execute(sql)  # no parameters: multiple statements are allowed
        except self._psycopg.Error as exc:
            raise BackendError(str(exc)) from exc

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._in_tx:
            yield
            return
        self._in_tx = True
        try:
            with self._conn.transaction():
                yield
        finally:
            self._in_tx = False

    def close(self) -> None:
        self._conn.close()

    def create_schema(self) -> None:
        self.script(_TABLES + _POSTGRES_TRIGGERS)


def is_postgres_url(location: str) -> bool:
    return location.startswith(("postgresql://", "postgres://"))


def connect(location: str | Path) -> Database:
    text = str(location)
    if is_postgres_url(text):
        return PostgresDatabase(text)
    return SQLiteDatabase(Path(location))
