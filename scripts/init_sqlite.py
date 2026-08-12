from __future__ import annotations

from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dvp_meeting_prep.config import configure_logging, get_settings
from dvp_meeting_prep.db import EXPECTED_TABLES, Database, DatabaseError


def main() -> int:
    configure_logging()
    start_total = time.monotonic()

    try:
        settings = get_settings()
    except Exception as exc:
        print(f"[CONFIG] FAILED to load configuration: {exc}")
        return 2

    print(f"[CONFIG] SQLite path: {settings.sqlite.db_path}")
    settings.sqlite.db_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[CONFIG] Parent directory ready: {settings.sqlite.db_path.parent}")

    database = Database(settings.sqlite)

    print("[DATABASE] Opening SQLite database")
    step_start = time.monotonic()
    try:
        with database.read() as conn:
            version_row = conn.execute("SELECT sqlite_version()").fetchone()
    except DatabaseError as exc:
        print(f"[DATABASE] FAILED to open database: {exc}")
        return 3
    print(f"[DATABASE] SQLite version: {version_row[0]} ({time.monotonic() - step_start:.2f}s)")

    print("[SCHEMA] Checking schema migrations")
    step_start = time.monotonic()
    try:
        applied = database.ensure_schema_ready()
    except DatabaseError as exc:
        print(f"[SCHEMA] FAILED to apply migrations: {exc}")
        return 4
    if not applied:
        print("[SCHEMA] No pending migrations -- schema already current.")
    print(f"[SCHEMA] Migration check complete ({time.monotonic() - step_start:.2f}s)")

    print("[SCHEMA] Verifying expected tables")
    tables = set(database.list_tables())
    missing_tables = [t for t in EXPECTED_TABLES if t not in tables]
    if missing_tables:
        print(f"[SCHEMA] FAILED: missing expected tables: {missing_tables}")
        return 5
    print(f"[SCHEMA] Found {len(EXPECTED_TABLES)} application tables")

    indexes = database.list_indexes()
    print(f"[SCHEMA] Found {len(indexes)} indexes")

    integrity = database.integrity_check()
    if integrity != ["ok"]:
        print(f"[SCHEMA] FAILED integrity check: {integrity}")
        return 6

    print(f"[COMPLETE] SQLite database is ready ({time.monotonic() - start_total:.2f}s total)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
