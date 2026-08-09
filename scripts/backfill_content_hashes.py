from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dvp_meeting_prep.db import get_supabase_client
from dvp_meeting_prep.ingest import CONSULTANT_SCORECARD_RAW_HASH_FIELDS, TABLEAU_HASH_FIELDS, _row_content_hash

PAGE_SIZE = 1000


def _fetch_all(client, table_name: str) -> list[dict]:
    rows: list[dict] = []
    start = 0
    while True:
        end = start + PAGE_SIZE - 1
        response = client.table(table_name).select("*").order("id").range(start, end).execute()
        batch = response.data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return rows


def backfill_and_dedupe(client, table_name: str, hash_fields: list[str]) -> dict[str, int]:
    """One-time migration: compute content_hash for rows ingested before the
    dedup column existed, then collapse any rows that turn out to share a hash
    (keeping the most recent id). Safe to run multiple times.
    """
    rows = _fetch_all(client, table_name)

    best_by_hash: dict[str, dict] = {}
    for row in rows:
        row_hash = _row_content_hash(row, hash_fields)
        existing = best_by_hash.get(row_hash)
        if existing is None or row["id"] > existing["id"]:
            best_by_hash[row_hash] = row

    for row_hash, row in best_by_hash.items():
        if row.get("content_hash") != row_hash:
            client.table(table_name).update({"content_hash": row_hash}).eq("id", row["id"]).execute()

    keep_ids = {row["id"] for row in best_by_hash.values()}
    delete_ids = [row["id"] for row in rows if row["id"] not in keep_ids]
    for start in range(0, len(delete_ids), 200):
        batch = delete_ids[start : start + 200]
        if batch:
            client.table(table_name).delete().in_("id", batch).execute()

    return {"total_rows_seen": len(rows), "unique_rows_kept": len(keep_ids), "duplicate_rows_deleted": len(delete_ids)}


def main() -> None:
    client = get_supabase_client()
    print("tableau_data:", backfill_and_dedupe(client, "tableau_data", TABLEAU_HASH_FIELDS))
    print("consultant_scorecard_raw:", backfill_and_dedupe(client, "consultant_scorecard_raw", CONSULTANT_SCORECARD_RAW_HASH_FIELDS))


if __name__ == "__main__":
    main()
