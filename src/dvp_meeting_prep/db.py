from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator, Sequence
import json
import logging
import sqlite3

from .config import PROJECT_ROOT, SQLiteConfig, get_settings

logger = logging.getLogger("dvp_meeting_prep.db")

MIGRATIONS_DIR = PROJECT_ROOT / "sql" / "migrations"

# The nine logical application tables (see docs/TESTING_GEMINI_SQLITE_MIGRATION.md).
EXPECTED_TABLES = [
    "salesforce_data",
    "salesforce_data_auto",
    "tableau_data",
    "consultant_scorecard_data",
    "consultant_scorecard_raw",
    "consultant_scorecard_monthly",
    "consultant_scorecard_metric",
    "upload_batches",
    "meeting_prep_documents",
]

EXPECTED_INDEXES = [
    "salesforce_data_advisor_name_idx",
    "salesforce_data_auto_advisor_name_idx",
    "tableau_data_advisor_name_idx",
    "tableau_data_content_hash_idx",
    "consultant_scorecard_data_advisor_name_idx",
    "consultant_scorecard_data_advisor_number_idx",
    "consultant_scorecard_raw_report_date_idx",
    "consultant_scorecard_raw_advisor_number_idx",
    "consultant_scorecard_raw_content_hash_idx",
    "consultant_scorecard_monthly_advisor_name_idx",
    "consultant_scorecard_monthly_advisor_number_idx",
    "consultant_scorecard_metric_scorecard_id_idx",
    "consultant_scorecard_metric_group_period_idx",
    "upload_batches_uploaded_at_idx",
    "meeting_prep_documents_advisor_name_idx",
]

# ADVISOR_SOURCE_MODE (see config.py) picks which of these the app treats as
# *the* Salesforce advisor/task table for search and meeting-prep generation
# -- "legacy" (the manually-provided spreadsheet, DATA_SOURCE=csv) or "auto"
# (a live Salesforce extraction, DATA_SOURCE=salesforce). Both tables share
# the exact same columns; only which rows are in them differs.
SALESFORCE_TABLE_BY_ADVISOR_SOURCE_MODE = {
    "legacy": "salesforce_data",
    "auto": "salesforce_data_auto",
}


class DatabaseError(RuntimeError):
    """Base class for all errors raised by the SQLite layer."""


class DatabaseNotWritableError(DatabaseError):
    """The database file or its parent directory is not writable."""


class DatabaseLockedError(DatabaseError):
    """The database remained locked past SQLITE_BUSY_TIMEOUT_MS."""


class DatabaseCorruptError(DatabaseError):
    """PRAGMA integrity_check reported a problem."""


class SchemaNotReadyError(DatabaseError):
    """A required table or index is missing -- run scripts/init_sqlite.py."""


class MigrationError(DatabaseError):
    """A migration failed to apply. It was rolled back and not recorded."""


class IntegrityError(DatabaseError):
    """A unique/foreign-key/check constraint was violated."""


# --- JSON / boolean serialization helpers ---------------------------------------
# SQLite has no native json/boolean types (see sql/schema.sql's translation
# notes) -- these are the two conversions the app actually needs, used at the
# exact call sites that read/write a JSON or boolean-typed legacy column,
# rather than a generic per-table type registry nothing else needs.


def json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def json_loads(value: Any) -> Any:
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    return json.loads(value)


def bool_to_int(value: bool | None) -> int | None:
    if value is None:
        return None
    return 1 if value else 0


def int_to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


# --- Connection / transaction layer ---------------------------------------------


@dataclass
class Database:
    config: SQLiteConfig

    def _connect(self) -> sqlite3.Connection:
        self.config.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # isolation_level=None => autocommit mode; every transaction below
            # is started/ended explicitly (BEGIN IMMEDIATE / COMMIT / ROLLBACK)
            # rather than relying on Python's implicit transaction handling.
            conn = sqlite3.connect(
                str(self.config.db_path),
                timeout=self.config.busy_timeout_ms / 1000,
                isolation_level=None,
            )
        except sqlite3.OperationalError as exc:
            raise DatabaseNotWritableError(
                f"Could not open SQLite database at {self.config.db_path}: {exc}"
            ) from exc

        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {int(self.config.busy_timeout_ms)}")
        conn.execute(f"PRAGMA journal_mode = {self.config.journal_mode}")
        conn.execute(f"PRAGMA synchronous = {self.config.synchronous}")
        conn.execute(f"PRAGMA foreign_keys = {'ON' if self.config.foreign_keys else 'OFF'}")
        if self.config.debug:
            conn.set_trace_callback(lambda sql: logger.debug("[SQLITE] %s", sql))
        return conn

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        """One short-lived read-only-by-convention connection per unit of work."""
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """One short-lived connection wrapping a single BEGIN IMMEDIATE ... COMMIT
        transaction. Any exception rolls the whole unit of work back -- e.g. a
        failed Salesforce replace never leaves salesforce_data partially empty.
        """
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            conn.close()
            if "locked" in str(exc).lower():
                raise DatabaseLockedError(
                    f"Database was locked past the {self.config.busy_timeout_ms}ms busy timeout: {exc}"
                ) from exc
            raise DatabaseError(str(exc)) from exc

        try:
            yield conn
            conn.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            conn.execute("ROLLBACK")
            raise IntegrityError(str(exc)) from exc
        except sqlite3.OperationalError as exc:
            conn.execute("ROLLBACK")
            if "locked" in str(exc).lower():
                raise DatabaseLockedError(
                    f"Database was locked past the {self.config.busy_timeout_ms}ms busy timeout: {exc}"
                ) from exc
            raise DatabaseError(str(exc)) from exc
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def health_check(self) -> dict[str, Any]:
        """Cheap liveness check: can we open the file and run a trivial query."""
        try:
            with self.read() as conn:
                conn.execute("SELECT 1").fetchone()
            return {"ok": True, "db_path": str(self.config.db_path)}
        except DatabaseError as exc:
            return {"ok": False, "db_path": str(self.config.db_path), "error": str(exc)}

    def list_tables(self) -> list[str]:
        with self.read() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        return [row["name"] for row in rows]

    def list_indexes(self) -> list[str]:
        with self.read() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        return [row["name"] for row in rows]

    def integrity_check(self) -> list[str]:
        with self.read() as conn:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
        return [row[0] for row in rows]

    def foreign_key_check(self) -> list[dict[str, Any]]:
        with self.read() as conn:
            rows = conn.execute("PRAGMA foreign_key_check").fetchall()
        return [dict(row) for row in rows]

    def foreign_keys_enabled(self) -> bool:
        with self.read() as conn:
            row = conn.execute("PRAGMA foreign_keys").fetchone()
        return bool(row[0]) if row is not None else False

    def applied_migrations(self) -> list[dict[str, Any]]:
        with self.read() as conn:
            try:
                rows = conn.execute("SELECT version, name, applied_at FROM schema_migrations ORDER BY version").fetchall()
            except sqlite3.OperationalError:
                return []
        return [dict(row) for row in rows]

    def ensure_schema_ready(self) -> list[int]:
        """Create missing tables/indexes and apply pending migrations.

        Safe to call on every application startup: never drops tables,
        deletes rows, or resets data -- see run_migrations().
        """
        return run_migrations(self)

    def require_schema_ready(self) -> None:
        """Raise SchemaNotReadyError if any expected table is missing.

        Used where auto-creating the schema would be surprising (e.g. a CLI
        verification script) -- callers that should self-heal use
        ensure_schema_ready() instead.
        """
        existing = set(self.list_tables())
        missing = [t for t in EXPECTED_TABLES if t not in existing]
        if missing:
            raise SchemaNotReadyError(
                f"Missing expected tables: {missing}. Run scripts/init_sqlite.py to create the schema."
            )


@lru_cache(maxsize=1)
def get_database() -> Database:
    settings = get_settings()
    return Database(settings.sqlite)


# --- Migration runner -------------------------------------------------------------


def _bootstrap_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          applied_at TEXT NOT NULL
        )
        """
    )


def _strip_sql_comments(sql_text: str) -> str:
    kept_lines = [line for line in sql_text.splitlines() if not line.strip().startswith("--")]
    return "\n".join(kept_lines)


def _split_sql_statements(sql_text: str) -> list[str]:
    """Split a script into individual statements so each can be executed with
    conn.execute() inside our own transaction. Deliberately not using
    sqlite3's executescript(): it issues an implicit COMMIT before running,
    and does not run the script as a single transaction, which would make a
    failed migration partially commit instead of rolling back cleanly.

    Good enough (not a general SQL parser) for this repo's migration files,
    which contain only CREATE TABLE/INDEX statements -- quote-aware only to
    the extent of not splitting on a ';' inside a '...' string literal.
    """
    text = _strip_sql_comments(sql_text)
    statements: list[str] = []
    current: list[str] = []
    in_string = False
    for char in text:
        current.append(char)
        if char == "'":
            in_string = not in_string
        elif char == ";" and not in_string:
            statement = "".join(current).strip()
            if statement and statement != ";":
                statements.append(statement)
            current = []
    trailing = "".join(current).strip()
    if trailing:
        statements.append(trailing)
    return statements


@dataclass
class MigrationFile:
    version: int
    name: str
    path: Path


def _discover_migration_files() -> list[MigrationFile]:
    if not MIGRATIONS_DIR.exists():
        return []
    discovered: list[MigrationFile] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version_str, _, name = path.stem.partition("_")
        try:
            version = int(version_str)
        except ValueError as exc:
            raise MigrationError(
                f"Migration filename {path.name!r} must start with a numeric version (e.g. 0001_initial.sql)."
            ) from exc
        discovered.append(MigrationFile(version=version, name=name or path.stem, path=path))
    return sorted(discovered, key=lambda m: m.version)


def run_migrations(database: Database) -> list[int]:
    """Apply every not-yet-applied migration in sql/migrations/, in order.

    Each migration runs in its own BEGIN IMMEDIATE ... COMMIT transaction; a
    failing statement rolls back that entire migration and it is not
    recorded in schema_migrations, so a retry (after fixing the cause) will
    attempt it again from scratch. Never touches migrations already recorded
    as applied. Returns the list of version numbers newly applied.
    """
    with database.write() as conn:
        _bootstrap_migrations_table(conn)
        already_applied = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()}

    applied_now: list[int] = []
    for migration in _discover_migration_files():
        if migration.version in already_applied:
            continue

        label = f"{migration.version:04d}_{migration.name}"
        print(f"[MIGRATION] Applying {label}")
        statements = _split_sql_statements(migration.path.read_text(encoding="utf-8"))
        with database.write() as conn:
            try:
                for statement in statements:
                    conn.execute(statement)
            except sqlite3.Error as exc:
                raise MigrationError(
                    f"Migration {migration.version:04d}_{migration.name} failed and was rolled back: {exc}"
                ) from exc
            conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
                (migration.version, migration.name),
            )
        applied_now.append(migration.version)
        print(f"[MIGRATION] {label} complete")
        logger.info("[MIGRATION] %s applied", label)

    return applied_now


# --- Generic parameterized helpers -------------------------------------------------


def fetch_all(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    cursor = conn.execute(sql, params)
    return [dict(row) for row in cursor.fetchall()]


def fetch_one(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
    cursor = conn.execute(sql, params)
    row = cursor.fetchone()
    return dict(row) if row is not None else None


def count_rows(conn: sqlite3.Connection, table_name: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS n FROM {table_name}").fetchone()
    return int(row["n"])


# --- Higher-level row operations shared by ingest.py -------------------------------


@dataclass
class UpsertResult:
    rows_inserted: int = 0
    rows_skipped_duplicate: int = 0


def replace_table_rows(database: Database, table_name: str, rows: list[dict[str, Any]]) -> int:
    """Atomically replace every row in `table_name`: DELETE FROM ... then
    INSERT all of `rows`, in one transaction. If the insert half fails, the
    delete is rolled back too, so the previous complete dataset is never
    left partially or fully empty.
    """
    with database.write() as conn:
        conn.execute(f"DELETE FROM {table_name}")
        if rows:
            columns = list(rows[0].keys())
            placeholders = ", ".join(["?"] * len(columns))
            column_list = ", ".join(columns)
            conn.executemany(
                f"INSERT INTO {table_name} ({column_list}) VALUES ({placeholders})",
                [[row.get(col) for col in columns] for row in rows],
            )
    return len(rows)


def upsert_rows_dedup_by_conflict_column(
    database: Database,
    table_name: str,
    rows: list[dict[str, Any]],
    conflict_column: str,
) -> UpsertResult:
    """INSERT ... ON CONFLICT(conflict_column) DO NOTHING, one row at a time
    inside a single transaction, counting new vs. duplicate via each
    statement's rowcount (0 = the unique constraint hit and nothing changed,
    i.e. a duplicate; 1 = a fresh row landed). Deliberately avoids a
    SELECT-then-INSERT dedup check: the unique index + ON CONFLICT already
    gives atomically-correct behavior without an extra round trip.
    """
    result = UpsertResult()
    if not rows:
        return result

    columns = list(rows[0].keys())
    column_list = ", ".join(columns)
    placeholders = ", ".join(["?"] * len(columns))
    sql = f"INSERT INTO {table_name} ({column_list}) VALUES ({placeholders}) ON CONFLICT({conflict_column}) DO NOTHING"

    with database.write() as conn:
        for row in rows:
            cursor = conn.execute(sql, [row.get(col) for col in columns])
            if cursor.rowcount and cursor.rowcount > 0:
                result.rows_inserted += 1
            else:
                result.rows_skipped_duplicate += 1

    return result
