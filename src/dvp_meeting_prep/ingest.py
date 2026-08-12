from __future__ import annotations

from pathlib import Path
import hashlib
import json
from typing import Any

from .data_source import load_salesforce_source_data
from .db import (
    Database,
    UpsertResult,
    bool_to_int,
    json_dumps,
    replace_table_rows,
    upsert_rows_dedup_by_conflict_column,
)
from .files import parse_consultant_scorecard, read_consultant_scorecard_rows, read_tableau_rows

# Columns present on every row that carries a nested/JSON raw_payload -- see
# _prepare_row_for_insert(). SQLite has no native JSON type (sql/schema.sql),
# so this is the one place that conversion happens for every table.
_JSON_COLUMN = "raw_payload"


def _prepare_row_for_insert(row: dict[str, Any]) -> dict[str, Any]:
    prepared = dict(row)
    if _JSON_COLUMN in prepared:
        prepared[_JSON_COLUMN] = json_dumps(prepared[_JSON_COLUMN])
    return prepared


def _insert_rows(database: Database, table_name: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    columns = list(rows[0].keys())
    column_list = ", ".join(columns)
    placeholders = ", ".join(["?"] * len(columns))
    with database.write() as conn:
        conn.executemany(
            f"INSERT INTO {table_name} ({column_list}) VALUES ({placeholders})",
            [[row.get(col) for col in columns] for row in rows],
        )


def ingest_rows(database: Database, table_name: str, rows: list[dict[str, Any]], replace_existing: bool = True) -> int:
    """Insert `rows` into `table_name`.

    When replace_existing (the default, matching pre-migration behavior),
    the whole table is atomically replaced -- see db.py:replace_table_rows --
    so a failed insert never leaves the table partially or fully empty.
    """
    prepared = [_prepare_row_for_insert(row) for row in rows]

    if replace_existing:
        return replace_table_rows(database, table_name, prepared)

    _insert_rows(database, table_name, prepared)
    return len(prepared)


def _normalize_for_hash(value: Any) -> Any:
    """Make a value stable for hashing regardless of round-tripping through
    the database. A whole-number Python float (e.g. 4.0) serializes as
    "4.0", but the same value read back after a JSON/SQLite round-trip can
    come back as the int 4. Without normalizing, the same logical row would
    hash differently before and after being stored, breaking dedup.
    Recurses into dicts/lists since hashed values often include a nested
    raw_payload blob.
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

MONTHLY_COLUMNS = [
    "source_file",
    "report_date",
    "advisor_number",
    "advisor_name",
    "area",
    "ro_number",
    "region",
    "division",
    "base_achievement_level",
    "etf_completed_approved",
    "designation",
    "sales_start_date",
    "termination_date",
    "tenure_category",
    "pwm_indicator",
    "dealer_code",
    "insurance_expiry_date",
    "key_driver_score",
    "raw_payload",
]


def _ingest_scorecard_monthly_and_metrics(database: Database, parsed: dict[str, Any], *, replace_existing: bool) -> dict[str, int]:
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

    monthly_id_map: dict[tuple[str, str], int] = {}
    monthly_count = 0
    metric_count = 0

    # One transaction for the whole monthly+metric operation: if inserting
    # metrics fails partway through, the monthly upserts in the same run are
    # rolled back too, instead of leaving monthly rows with no metrics.
    with database.write() as conn:
        if replace_existing:
            conn.execute("DELETE FROM consultant_scorecard_metric")
            conn.execute("DELETE FROM consultant_scorecard_monthly")

        if monthly_rows:
            set_clause = ", ".join(f"{col} = excluded.{col}" for col in MONTHLY_COLUMNS if col not in ("report_date", "advisor_number"))
            column_list = ", ".join(MONTHLY_COLUMNS)
            placeholders = ", ".join(["?"] * len(MONTHLY_COLUMNS))
            upsert_sql = (
                f"INSERT INTO consultant_scorecard_monthly ({column_list}) VALUES ({placeholders}) "
                f"ON CONFLICT(report_date, advisor_number) DO UPDATE SET {set_clause} "
                "RETURNING id, report_date, advisor_number"
            )
            for row in monthly_rows:
                item = dict(row)
                item.pop("source_row_number", None)
                item["etf_completed_approved"] = bool_to_int(item.get("etf_completed_approved"))
                item["pwm_indicator"] = bool_to_int(item.get("pwm_indicator"))
                item["raw_payload"] = json_dumps(item.get("raw_payload"))
                values = [item.get(col) for col in MONTHLY_COLUMNS]
                cursor = conn.execute(upsert_sql, values)
                returned = cursor.fetchone()
                if returned is not None:
                    monthly_id_map[(str(returned["report_date"]), str(returned["advisor_number"]))] = int(returned["id"])
                monthly_count += 1

        # Metrics are re-derived from the monthly row each ingest, so clear out
        # any previous metrics for the scorecards touched in this run before
        # re-inserting (metrics have no natural unique key of their own).
        touched_scorecard_ids = sorted(set(monthly_id_map.values()))
        if touched_scorecard_ids:
            placeholders = ", ".join(["?"] * len(touched_scorecard_ids))
            conn.execute(
                f"DELETE FROM consultant_scorecard_metric WHERE scorecard_id IN ({placeholders})",
                touched_scorecard_ids,
            )

        report_date = parsed.get("report_date")
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

        if metric_insert_rows:
            columns = list(metric_insert_rows[0].keys())
            column_list = ", ".join(columns)
            placeholders = ", ".join(["?"] * len(columns))
            conn.executemany(
                f"INSERT INTO consultant_scorecard_metric ({column_list}) VALUES ({placeholders})",
                [[row.get(col) for col in columns] for row in metric_insert_rows],
            )
        metric_count = len(metric_insert_rows)

    return {
        "consultant_scorecard_monthly": monthly_count,
        "consultant_scorecard_metric": metric_count,
    }


def _ingest_scorecard_raw(database: Database, raw_rows_all: list[dict[str, Any]], *, replace_existing: bool) -> dict[str, int]:
    for row in raw_rows_all:
        row["content_hash"] = _row_content_hash(row, CONSULTANT_SCORECARD_RAW_HASH_FIELDS)

    deduped_rows, intra_batch_duplicates = _dedupe_rows_by_hash(raw_rows_all)
    prepared_rows = [_prepare_row_for_insert(row) for row in deduped_rows]

    if replace_existing:
        inserted = replace_table_rows(database, "consultant_scorecard_raw", prepared_rows)
        return {
            "rows_parsed": len(raw_rows_all),
            "rows_inserted": inserted,
            "rows_skipped_duplicate": intra_batch_duplicates,
        }

    result = upsert_rows_dedup_by_conflict_column(database, "consultant_scorecard_raw", prepared_rows, "content_hash")
    return {
        "rows_parsed": len(raw_rows_all),
        "rows_inserted": result.rows_inserted,
        "rows_skipped_duplicate": intra_batch_duplicates + result.rows_skipped_duplicate,
    }


def ingest_consultant_scorecard_structured(
    database: Database, scorecard_path: str | Path, replace_existing: bool = True, *, source_file_name: str | None = None
) -> dict[str, int]:
    """Full ingest of a consultant scorecard workbook (raw + monthly + metric tables).

    Safe to call repeatedly: raw rows dedupe on content hash and monthly/metric
    rows upsert on (report_date, advisor_number), so re-running with the same
    file never creates duplicate rows.
    """
    parsed = parse_consultant_scorecard(scorecard_path, source_file_name=source_file_name)

    raw_counts = _ingest_scorecard_raw(database, parsed["raw_rows"], replace_existing=replace_existing)
    monthly_metric_counts = _ingest_scorecard_monthly_and_metrics(database, parsed, replace_existing=replace_existing)

    return {
        "consultant_scorecard_raw": raw_counts["rows_inserted"],
        "consultant_scorecard_raw_duplicates": raw_counts["rows_skipped_duplicate"],
        **monthly_metric_counts,
    }


def ingest_consultant_scorecard_upload(database: Database, scorecard_path: str | Path, *, source_file_name: str | None = None) -> dict[str, int]:
    """Ingest a consultant scorecard workbook uploaded through the /upload UI.

    `source_file_name` should be the original uploaded file name, not the
    temp path it was saved to -- the temp path's random name would otherwise
    make identical uploads hash differently every time and defeat dedup.

    Never deletes existing data: raw rows dedupe on content hash, monthly rows
    upsert per advisor/report-date, so uploading the same file twice (or an
    overlapping export) is a no-op for rows that already exist.
    """
    parsed = parse_consultant_scorecard(scorecard_path, source_file_name=source_file_name)

    raw_counts = _ingest_scorecard_raw(database, parsed["raw_rows"], replace_existing=False)
    monthly_metric_counts = _ingest_scorecard_monthly_and_metrics(database, parsed, replace_existing=False)

    return {
        "rows_parsed": raw_counts["rows_parsed"],
        "rows_inserted": raw_counts["rows_inserted"],
        "rows_skipped_duplicate": raw_counts["rows_skipped_duplicate"],
        "consultant_scorecard_monthly_upserted": monthly_metric_counts["consultant_scorecard_monthly"],
        "consultant_scorecard_metric_upserted": monthly_metric_counts["consultant_scorecard_metric"],
    }


def ingest_tableau_upload(database: Database, tableau_path: str | Path) -> dict[str, int]:
    """Ingest a Tableau export CSV uploaded through the /upload UI.

    Rows dedupe on a content hash of their data fields, so re-uploading the
    same export (or one with overlapping rows) never creates duplicates.
    """
    rows = read_tableau_rows(tableau_path)
    for row in rows:
        row["content_hash"] = _row_content_hash(row, TABLEAU_HASH_FIELDS)

    deduped_rows, intra_batch_duplicates = _dedupe_rows_by_hash(rows)
    prepared_rows = [_prepare_row_for_insert(row) for row in deduped_rows]

    result: UpsertResult = upsert_rows_dedup_by_conflict_column(database, "tableau_data", prepared_rows, "content_hash")

    return {
        "rows_parsed": len(rows),
        "rows_inserted": result.rows_inserted,
        "rows_skipped_duplicate": intra_batch_duplicates + result.rows_skipped_duplicate,
    }


def ingest_all_sources(
    database: Database,
    salesforce_path: str | None,
    tableau_path: str,
    scorecard_path: str,
    replace_existing: bool = True,
) -> dict[str, int]:
    """`salesforce_path` is only used when DATA_SOURCE=csv (the local .xlsx
    fallback); when DATA_SOURCE=salesforce (the default) advisor/task data is
    pulled from the Salesforce API instead and this path is ignored. See
    data_source.py for the selection logic.
    """
    salesforce_rows = load_salesforce_source_data(csv_path=salesforce_path)
    tableau_rows = read_tableau_rows(tableau_path)
    scorecard_rows = read_consultant_scorecard_rows(scorecard_path)
    scorecard_counts = ingest_consultant_scorecard_structured(database, scorecard_path, replace_existing=replace_existing)

    counts = {
        "salesforce_data": ingest_rows(database, "salesforce_data", salesforce_rows, replace_existing=replace_existing),
        "tableau_data": ingest_rows(database, "tableau_data", tableau_rows, replace_existing=replace_existing),
        "consultant_scorecard_data": ingest_rows(
            database,
            "consultant_scorecard_data",
            scorecard_rows,
            replace_existing=replace_existing,
        ),
    }
    counts.update(scorecard_counts)
    return counts
