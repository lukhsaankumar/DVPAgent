from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dvp_meeting_prep.config import configure_logging, get_settings
from dvp_meeting_prep.db import EXPECTED_INDEXES, EXPECTED_TABLES, Database, DatabaseError, count_rows


def main() -> int:
    configure_logging()
    ok = True

    try:
        settings = get_settings()
    except Exception as exc:
        print(f"[CONFIG] FAILED to load configuration: {exc}")
        return 2

    db_path = settings.sqlite.db_path
    print(f"[DATABASE] Path: {db_path}")

    if not db_path.exists():
        print("[DATABASE] FAILED: database file does not exist yet. Run scripts/init_sqlite.py first.")
        return 3

    print(f"[DATABASE] File size: {db_path.stat().st_size} bytes")

    database = Database(settings.sqlite)
    try:
        with database.read() as conn:
            version_row = conn.execute("SELECT sqlite_version()").fetchone()
    except DatabaseError as exc:
        print(f"[DATABASE] FAILED to open database: {exc}")
        return 4
    print(f"[DATABASE] SQLite version: {version_row[0]}")

    print("\n[INTEGRITY] Running PRAGMA integrity_check")
    integrity = database.integrity_check()
    if integrity == ["ok"]:
        print("[INTEGRITY] OK")
    else:
        print(f"[INTEGRITY] FAILED: {integrity}")
        ok = False

    print("\n[FOREIGN KEYS] Checking PRAGMA foreign_keys")
    if database.foreign_keys_enabled():
        print("[FOREIGN KEYS] Enabled: true")
    else:
        print("[FOREIGN KEYS] FAILED: foreign_keys is OFF for this connection")
        ok = False

    print("[FOREIGN KEYS] Running PRAGMA foreign_key_check")
    fk_violations = database.foreign_key_check()
    if not fk_violations:
        print("[FOREIGN KEYS] No violations")
    else:
        print(f"[FOREIGN KEYS] FAILED: {len(fk_violations)} violation(s) found")
        ok = False

    print("\n[MIGRATIONS] Applied migrations:")
    migrations = database.applied_migrations()
    if not migrations:
        print("  (none)")
    for migration in migrations:
        print(f"  {migration['version']:04d}  {migration['name']}  {migration['applied_at']}")

    print("\n[SCHEMA] Verifying expected tables")
    existing_tables = set(database.list_tables())
    missing_tables = [t for t in EXPECTED_TABLES if t not in existing_tables]
    if missing_tables:
        print(f"[SCHEMA] FAILED: missing tables: {missing_tables}")
        ok = False
    else:
        print(f"[SCHEMA] All {len(EXPECTED_TABLES)} expected tables present")

    print("\n[SCHEMA] Verifying expected indexes")
    existing_indexes = set(database.list_indexes())
    missing_indexes = [i for i in EXPECTED_INDEXES if i not in existing_indexes]
    if missing_indexes:
        print(f"[SCHEMA] FAILED: missing indexes: {missing_indexes}")
        ok = False
    else:
        print(f"[SCHEMA] All {len(EXPECTED_INDEXES)} expected indexes present")

    print("\n[ROW COUNTS]")
    with database.read() as conn:
        for table in EXPECTED_TABLES:
            if table in existing_tables:
                print(f"  {table}: {count_rows(conn, table)}")
            else:
                print(f"  {table}: (table missing)")

    print()
    if ok:
        print("[COMPLETE] All checks passed")
        return 0
    print("[COMPLETE] One or more checks FAILED (see above)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
