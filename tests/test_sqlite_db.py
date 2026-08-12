from __future__ import annotations

import pytest

from dvp_meeting_prep.config import SQLiteConfig
from dvp_meeting_prep.db import (
    EXPECTED_INDEXES,
    EXPECTED_TABLES,
    Database,
    DatabaseLockedError,
    DatabaseNotWritableError,
    IntegrityError,
    MigrationError,
    SchemaNotReadyError,
    fetch_all,
)


def _config(db_path, **overrides) -> SQLiteConfig:
    defaults = dict(
        db_path=db_path,
        busy_timeout_ms=2000,
        journal_mode="WAL",
        foreign_keys=True,
        synchronous="NORMAL",
        debug=False,
    )
    defaults.update(overrides)
    return SQLiteConfig(**defaults)


def test_fresh_init_creates_all_tables_and_indexes(sqlite_db):
    tables = set(sqlite_db.list_tables())
    for table in EXPECTED_TABLES:
        assert table in tables
    assert "schema_migrations" in tables

    indexes = set(sqlite_db.list_indexes())
    for index in EXPECTED_INDEXES:
        assert index in indexes

    sqlite_db.require_schema_ready()  # must not raise


def test_ensure_schema_ready_is_idempotent(sqlite_db):
    before_tables = sqlite_db.list_tables()
    before_indexes = sqlite_db.list_indexes()

    applied_again = sqlite_db.ensure_schema_ready()

    assert applied_again == []  # nothing new to apply
    assert sqlite_db.list_tables() == before_tables
    assert sqlite_db.list_indexes() == before_indexes


def test_migration_recorded_exactly_once(sqlite_db):
    applied = sqlite_db.applied_migrations()
    versions = [row["version"] for row in applied]
    assert versions.count(1) == 1

    sqlite_db.ensure_schema_ready()
    sqlite_db.ensure_schema_ready()

    applied_after = sqlite_db.applied_migrations()
    assert [row["version"] for row in applied_after].count(1) == 1


def test_require_schema_ready_raises_when_tables_missing(tmp_path):
    database = Database(_config(tmp_path / "empty.sqlite3"))
    with pytest.raises(SchemaNotReadyError, match="salesforce_data"):
        database.require_schema_ready()


def test_failed_migration_rolls_back_and_is_not_recorded(tmp_path, monkeypatch):
    from dvp_meeting_prep import db as db_module

    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "0001_good.sql").write_text(
        "CREATE TABLE IF NOT EXISTS good_table (id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )
    (migrations_dir / "0002_bad.sql").write_text(
        "CREATE TABLE partial_table (id INTEGER PRIMARY KEY);\n"
        "THIS IS NOT VALID SQL;",
        encoding="utf-8",
    )
    monkeypatch.setattr(db_module, "MIGRATIONS_DIR", migrations_dir)

    database = Database(_config(tmp_path / "rollback.sqlite3"))
    with pytest.raises(MigrationError):
        database.ensure_schema_ready()

    tables = set(database.list_tables())
    assert "good_table" in tables  # migration 0001 succeeded and was recorded
    assert "partial_table" not in tables  # migration 0002's first statement was rolled back

    versions = [row["version"] for row in database.applied_migrations()]
    assert versions == [1]  # 0002 was never recorded as applied


def test_foreign_keys_enabled_by_default(sqlite_db):
    assert sqlite_db.foreign_keys_enabled() is True


def test_foreign_key_violation_is_rejected_and_rolled_back(sqlite_db):
    with pytest.raises(IntegrityError):
        with sqlite_db.write() as conn:
            conn.execute(
                """
                INSERT INTO consultant_scorecard_metric
                    (scorecard_id, source_column, metric_group, metric_name)
                VALUES (?, ?, ?, ?)
                """,
                (999999, "Z", "Assets", "Total Assets"),
            )

    with sqlite_db.read() as conn:
        count = conn.execute("SELECT COUNT(*) FROM consultant_scorecard_metric").fetchone()[0]
    assert count == 0


def test_health_check_ok_when_reachable(sqlite_db):
    result = sqlite_db.health_check()
    assert result == {"ok": True, "db_path": str(sqlite_db.config.db_path)}


def test_health_check_reports_error_when_db_path_is_unusable(tmp_path):
    # A directory can never be opened as a SQLite file -- sqlite3.connect()
    # raises OperationalError, which _connect() translates to
    # DatabaseNotWritableError, which health_check() catches.
    database = Database(_config(tmp_path))
    result = database.health_check()
    assert result["ok"] is False
    assert "error" in result


def test_connect_raises_databasenotwritableerror_for_unusable_path(tmp_path):
    database = Database(_config(tmp_path))
    with pytest.raises(DatabaseNotWritableError):
        with database.read():
            pass


def test_sql_injection_as_data_is_safe(sqlite_db):
    malicious_name = "Robert'); DROP TABLE salesforce_data;--"
    with sqlite_db.write() as conn:
        conn.execute(
            "INSERT INTO salesforce_data (advisor_name, subject) VALUES (?, ?)",
            (malicious_name, "test"),
        )

    assert "salesforce_data" in sqlite_db.list_tables()  # table survived

    with sqlite_db.read() as conn:
        rows = fetch_all(conn, "SELECT * FROM salesforce_data WHERE advisor_name = ?", (malicious_name,))
    assert len(rows) == 1
    assert rows[0]["advisor_name"] == malicious_name  # stored/returned as literal data


def test_parent_directory_is_auto_created(tmp_path):
    nested_path = tmp_path / "nested" / "dirs" / "db.sqlite3"
    database = Database(_config(nested_path))
    database.ensure_schema_ready()
    assert nested_path.exists()
    assert nested_path.parent.is_dir()


def test_locked_database_raises_databaselockederror(tmp_path):
    db_path = tmp_path / "locked.sqlite3"
    writer_db = Database(_config(db_path))
    writer_db.ensure_schema_ready()

    holder_conn = writer_db._connect()
    holder_conn.execute("BEGIN IMMEDIATE")
    holder_conn.execute("INSERT INTO salesforce_data (advisor_name) VALUES ('holder')")
    try:
        contender = Database(_config(db_path, busy_timeout_ms=200))
        with pytest.raises(DatabaseLockedError):
            with contender.write() as conn:
                conn.execute("INSERT INTO salesforce_data (advisor_name) VALUES ('contender')")
    finally:
        holder_conn.execute("ROLLBACK")
        holder_conn.close()


def test_writes_are_transactional_and_roll_back_on_exception(sqlite_db):
    class _Boom(Exception):
        pass

    with pytest.raises(_Boom):
        with sqlite_db.write() as conn:
            conn.execute("INSERT INTO salesforce_data (advisor_name) VALUES ('should not persist')")
            raise _Boom("simulated failure mid-transaction")

    with sqlite_db.read() as conn:
        count = conn.execute("SELECT COUNT(*) FROM salesforce_data").fetchone()[0]
    assert count == 0


def test_deterministic_ordering_by_ingested_at(sqlite_db):
    with sqlite_db.write() as conn:
        conn.execute(
            "INSERT INTO tableau_data (advisor_name, content_hash, ingested_at) VALUES (?, ?, ?)",
            ("Avery Benton", "hash-1", "2026-01-01T00:00:00.000000Z"),
        )
        conn.execute(
            "INSERT INTO tableau_data (advisor_name, content_hash, ingested_at) VALUES (?, ?, ?)",
            ("Avery Benton", "hash-2", "2026-02-01T00:00:00.000000Z"),
        )
        conn.execute(
            "INSERT INTO tableau_data (advisor_name, content_hash, ingested_at) VALUES (?, ?, ?)",
            ("Avery Benton", "hash-3", "2026-03-01T00:00:00.000000Z"),
        )

    for _ in range(3):  # repeated calls must always return the same order
        with sqlite_db.read() as conn:
            rows = fetch_all(
                conn,
                "SELECT content_hash FROM tableau_data WHERE advisor_name = ? ORDER BY ingested_at DESC",
                ("Avery Benton",),
            )
        assert [row["content_hash"] for row in rows] == ["hash-3", "hash-2", "hash-1"]


def test_data_survives_repeated_schema_init(sqlite_db):
    with sqlite_db.write() as conn:
        conn.execute("INSERT INTO salesforce_data (advisor_name) VALUES ('Persistent Advisor')")

    sqlite_db.ensure_schema_ready()
    sqlite_db.ensure_schema_ready()

    with sqlite_db.read() as conn:
        count = conn.execute("SELECT COUNT(*) FROM salesforce_data").fetchone()[0]
    assert count == 1


def test_no_supabase_import_anywhere_in_db_module():
    import sys

    from dvp_meeting_prep import db as db_module

    assert not hasattr(db_module, "supabase")
    assert "supabase" not in sys.modules
