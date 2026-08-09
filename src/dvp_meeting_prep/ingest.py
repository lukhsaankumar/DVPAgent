from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import hashlib
import json
import time
from typing import Any

from .files import parse_consultant_scorecard, read_consultant_scorecard_rows, read_salesforce_rows, read_tableau_rows


def chunked(items: list[dict[str, Any]], size: int = 100) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def ingest_rows(
    client: Any,
    table_name: str,
    rows: list[dict[str, Any]],
    replace_existing: bool = True,
    *,
    batch_size: int = 100,
    max_retries: int = 3,
) -> int:
    if not rows:
        return 0

    if replace_existing:
        client.table(table_name).delete().gte("id", 0).execute()

    inserted = 0
    for batch in chunked(rows, size=batch_size):
        attempt = 0
        while True:
            try:
                client.table(table_name).insert(batch).execute()
                break
            except Exception:
                attempt += 1
                if attempt >= max_retries:
                    raise
                time.sleep(0.5 * attempt)
        inserted += len(batch)
    return inserted


def _chunk_values(values: list[Any], size: int = 100) -> Iterable[list[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _normalize_for_hash(value: Any) -> Any:
    """Make a value stable for hashing across a Postgres JSONB round-trip.

    A whole-number Python float (e.g. 4.0) serializes as "4.0", but the same
    value read back from a jsonb column can come back as the int 4. Without
    normalizing, the same logical row would hash differently before and
    after being stored, breaking dedup. Recurses into dicts/lists since
    hashed values often include a nested raw_payload blob.
    """
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _normalize_for_hash(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_for_hash(item) for item in value]
    return value


def _row_content_hash(row: dict[str, Any], keys: list[str]) -> str:
    normalized = {key: _normalize_for_hash(row.get(key)) for key in keys}
    encoded = json.dumps(normalized, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _dedupe_rows_by_hash(rows: list[dict[str, Any]], hash_key: str = "content_hash") -> tuple[list[dict[str, Any]], int]:
    """Drop rows that repeat a content_hash already seen earlier in the same batch.

    Returns (deduped_rows, intra_batch_duplicate_count).
    """
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    duplicates = 0
    for row in rows:
        row_hash = row[hash_key]
        if row_hash in seen:
            duplicates += 1
            continue
        seen.add(row_hash)
        deduped.append(row)
    return deduped, duplicates


def _existing_hashes(client: Any, table_name: str, hashes: list[str], hash_key: str = "content_hash") -> set[str]:
    found: set[str] = set()
    for batch in _chunk_values(hashes, size=200):
        if not batch:
            continue
        response = client.table(table_name).select(hash_key).in_(hash_key, batch).execute()
        for row in response.data or []:
            value = row.get(hash_key)
            if value:
                found.add(value)
    return found


def _is_missing_table_error(exc: Exception) -> bool:
    message = str(exc)
    return "PGRST205" in message or "Could not find the table 'public." in message


def _table_exists(client: Any, table_name: str) -> bool:
    try:
        client.table(table_name).select("id").limit(1).execute()
        return True
    except Exception as exc:
        if _is_missing_table_error(exc):
            return False
        raise


def _ensure_structured_scorecard_tables(client: Any) -> None:
    required_tables = [
        "consultant_scorecard_raw",
        "consultant_scorecard_monthly",
        "consultant_scorecard_metric",
    ]
    missing = [table for table in required_tables if not _table_exists(client, table)]
    if missing:
        missing_text = ", ".join(missing)
        raise RuntimeError(
            "Missing required structured consultant scorecard tables: "
            f"{missing_text}. Run the latest SQL migration from sql/schema.sql in Supabase SQL Editor, then rerun ingestion."
        )


TABLEAU_HASH_FIELDS = [
    "advisor_name",
    "advisor_name_number",
    "segment",
    "date",
    "area_name",
    "region",
    "measure_names",
    "account_count_fund_formatted",
    "client_count_fund_formatted",
    "fund_formatted",
    "approved_to_buy",
    "area",
    "division_manager",
    "fund_family",
    "investment_vehicle",
    "pwm",
    "region_name",
    "measure_values",
]

CONSULTANT_SCORECARD_RAW_HASH_FIELDS = [
    "source_file",
    "sheet_name",
    "report_date",
    "source_row_number",
    "advisor_number",
    "advisor_name",
    "raw_payload",
]


def _ingest_scorecard_monthly_and_metrics(client: Any, parsed: dict[str, Any], *, replace_existing: bool) -> dict[str, int]:
    monthly_rows_all = parsed["monthly_rows"]
    metric_rows_all = parsed["metric_rows"]

    # Keep one canonical monthly row per (report_date, advisor_number) to satisfy
    # the unique constraint while preserving all source rows in consultant_scorecard_raw.
    monthly_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    canonical_row_number_by_key: dict[tuple[str, str], int] = {}
    for row in monthly_rows_all:
        advisor_number = row.get("advisor_number")
        report_date = row.get("report_date")
        source_row_number = row.get("source_row_number")
        if advisor_number is None or report_date is None or source_row_number is None:
            continue
        key = (str(report_date), str(advisor_number))
        if key not in monthly_by_key:
            monthly_by_key[key] = dict(row)
            canonical_row_number_by_key[key] = int(source_row_number)

    monthly_rows = list(monthly_by_key.values())

    metric_rows: list[dict[str, Any]] = []
    for metric in metric_rows_all:
        advisor_number = metric.get("advisor_number")
        source_row_number = metric.get("source_row_number")
        report_date = parsed.get("report_date")
        if advisor_number is None or source_row_number is None or report_date is None:
            continue
        key = (str(report_date), str(advisor_number))
        canonical_source_row = canonical_row_number_by_key.get(key)
        if canonical_source_row is None:
            continue
        if int(source_row_number) != int(canonical_source_row):
            continue
        metric_rows.append(metric)

    if replace_existing:
        client.table("consultant_scorecard_metric").delete().gte("id", 0).execute()
        client.table("consultant_scorecard_monthly").delete().gte("id", 0).execute()

    monthly_count = 0
    if monthly_rows:
        monthly_insert_rows: list[dict[str, Any]] = []
        for row in monthly_rows:
            item = dict(row)
            item.pop("source_row_number", None)
            monthly_insert_rows.append(item)
        for batch in chunked(monthly_insert_rows, size=100):
            client.table("consultant_scorecard_monthly").upsert(batch, on_conflict="report_date,advisor_number").execute()
            monthly_count += len(batch)

    monthly_id_map: dict[tuple[str, str], int] = {}
    report_date = parsed.get("report_date")
    advisor_numbers = [row["advisor_number"] for row in monthly_rows if row.get("advisor_number")]
    if report_date and advisor_numbers:
        for number_batch in _chunk_values(advisor_numbers, size=100):
            resp = (
                client.table("consultant_scorecard_monthly")
                .select("id,report_date,advisor_number")
                .eq("report_date", report_date)
                .in_("advisor_number", number_batch)
                .execute()
            )
            for row in resp.data or []:
                key = (str(row.get("report_date")), str(row.get("advisor_number")))
                monthly_id_map[key] = int(row["id"])

    # Metrics are re-derived from the monthly row each ingest, so clear out any
    # previous metrics for the scorecards touched in this run before re-inserting
    # (metrics have no natural unique key of their own to upsert against).
    touched_scorecard_ids = sorted(set(monthly_id_map.values()))
    for id_batch in _chunk_values(touched_scorecard_ids, size=200):
        client.table("consultant_scorecard_metric").delete().in_("scorecard_id", id_batch).execute()

    metric_insert_rows: list[dict[str, Any]] = []
    for metric in metric_rows:
        key = (str(report_date), str(metric.get("advisor_number")))
        scorecard_id = monthly_id_map.get(key)
        if scorecard_id is None:
            continue
        item = dict(metric)
        item.pop("advisor_number", None)
        item.pop("source_row_number", None)
        item["scorecard_id"] = scorecard_id
        metric_insert_rows.append(item)

    metric_count = 0
    for batch in chunked(metric_insert_rows, size=200):
        client.table("consultant_scorecard_metric").insert(batch).execute()
        metric_count += len(batch)

    return {
        "consultant_scorecard_monthly": monthly_count,
        "consultant_scorecard_metric": metric_count,
    }


def _ingest_scorecard_raw(client: Any, raw_rows_all: list[dict[str, Any]], *, replace_existing: bool) -> dict[str, int]:
    for row in raw_rows_all:
        row["content_hash"] = _row_content_hash(row, CONSULTANT_SCORECARD_RAW_HASH_FIELDS)

    deduped_rows, intra_batch_duplicates = _dedupe_rows_by_hash(raw_rows_all)

    if replace_existing:
        client.table("consultant_scorecard_raw").delete().gte("id", 0).execute()
        existing_hashes: set[str] = set()
    else:
        existing_hashes = _existing_hashes(client, "consultant_scorecard_raw", [row["content_hash"] for row in deduped_rows])

    duplicate_count = intra_batch_duplicates + sum(1 for row in deduped_rows if row["content_hash"] in existing_hashes)
    new_count = len(deduped_rows) - sum(1 for row in deduped_rows if row["content_hash"] in existing_hashes)

    for batch in chunked(deduped_rows, size=100):
        client.table("consultant_scorecard_raw").upsert(batch, on_conflict="content_hash").execute()

    return {
        "rows_parsed": len(raw_rows_all),
        "rows_inserted": new_count,
        "rows_skipped_duplicate": duplicate_count,
    }


def ingest_consultant_scorecard_structured(
    client: Any, scorecard_path: str | Path, replace_existing: bool = True, *, source_file_name: str | None = None
) -> dict[str, int]:
    """Full ingest of a consultant scorecard workbook (raw + monthly + metric tables).

    Safe to call repeatedly: raw rows dedupe on content hash and monthly/metric
    rows upsert on (report_date, advisor_number), so re-running with the same
    file never creates duplicate rows.
    """
    _ensure_structured_scorecard_tables(client)
    parsed = parse_consultant_scorecard(scorecard_path, source_file_name=source_file_name)

    raw_counts = _ingest_scorecard_raw(client, parsed["raw_rows"], replace_existing=replace_existing)
    monthly_metric_counts = _ingest_scorecard_monthly_and_metrics(client, parsed, replace_existing=replace_existing)

    return {
        "consultant_scorecard_raw": raw_counts["rows_inserted"],
        "consultant_scorecard_raw_duplicates": raw_counts["rows_skipped_duplicate"],
        **monthly_metric_counts,
    }


def ingest_consultant_scorecard_upload(client: Any, scorecard_path: str | Path, *, source_file_name: str | None = None) -> dict[str, int]:
    """Ingest a consultant scorecard workbook uploaded through the /upload UI.

    `source_file_name` should be the original uploaded file name, not the
    temp path it was saved to -- the temp path's random name would otherwise
    make identical uploads hash differently every time and defeat dedup.

    Never deletes existing data: raw rows dedupe on content hash, monthly rows
    upsert per advisor/report-date, so uploading the same file twice (or an
    overlapping export) is a no-op for rows that already exist.
    """
    _ensure_structured_scorecard_tables(client)
    parsed = parse_consultant_scorecard(scorecard_path, source_file_name=source_file_name)

    raw_counts = _ingest_scorecard_raw(client, parsed["raw_rows"], replace_existing=False)
    monthly_metric_counts = _ingest_scorecard_monthly_and_metrics(client, parsed, replace_existing=False)

    return {
        "rows_parsed": raw_counts["rows_parsed"],
        "rows_inserted": raw_counts["rows_inserted"],
        "rows_skipped_duplicate": raw_counts["rows_skipped_duplicate"],
        "consultant_scorecard_monthly_upserted": monthly_metric_counts["consultant_scorecard_monthly"],
        "consultant_scorecard_metric_upserted": monthly_metric_counts["consultant_scorecard_metric"],
    }


def ingest_tableau_upload(client: Any, tableau_path: str | Path) -> dict[str, int]:
    """Ingest a Tableau export CSV uploaded through the /upload UI.

    Rows dedupe on a content hash of their data fields, so re-uploading the
    same export (or one with overlapping rows) never creates duplicates.
    """
    rows = read_tableau_rows(tableau_path)
    for row in rows:
        row["content_hash"] = _row_content_hash(row, TABLEAU_HASH_FIELDS)

    deduped_rows, intra_batch_duplicates = _dedupe_rows_by_hash(rows)
    existing_hashes = _existing_hashes(client, "tableau_data", [row["content_hash"] for row in deduped_rows])

    duplicate_count = intra_batch_duplicates + sum(1 for row in deduped_rows if row["content_hash"] in existing_hashes)
    new_count = len(deduped_rows) - sum(1 for row in deduped_rows if row["content_hash"] in existing_hashes)

    for batch in chunked(deduped_rows, size=200):
        client.table("tableau_data").upsert(batch, on_conflict="content_hash").execute()

    return {
        "rows_parsed": len(rows),
        "rows_inserted": new_count,
        "rows_skipped_duplicate": duplicate_count,
    }


def ingest_all_sources(client: Any, salesforce_path: str, tableau_path: str, scorecard_path: str, replace_existing: bool = True) -> dict[str, int]:
    salesforce_rows = read_salesforce_rows(salesforce_path)
    tableau_rows = read_tableau_rows(tableau_path)
    scorecard_rows = read_consultant_scorecard_rows(scorecard_path)
    scorecard_counts = ingest_consultant_scorecard_structured(client, scorecard_path, replace_existing=replace_existing)

    counts = {
        "salesforce_data": ingest_rows(client, "salesforce_data", salesforce_rows, replace_existing=replace_existing),
        "tableau_data": ingest_rows(client, "tableau_data", tableau_rows, replace_existing=replace_existing),
        "consultant_scorecard_data": ingest_rows(
            client,
            "consultant_scorecard_data",
            scorecard_rows,
            replace_existing=replace_existing,
            batch_size=20,
        ),
    }
    counts.update(scorecard_counts)
    return counts
