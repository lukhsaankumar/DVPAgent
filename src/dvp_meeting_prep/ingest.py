from __future__ import annotations

from collections.abc import Iterable
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


def _chunk_values(values: list[str], size: int = 100) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


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


def ingest_consultant_scorecard_structured(client: Any, scorecard_path: str, replace_existing: bool = True) -> dict[str, int]:
    _ensure_structured_scorecard_tables(client)
    parsed = parse_consultant_scorecard(scorecard_path)
    raw_rows = parsed["raw_rows"]
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
        # Choose the first occurrence deterministically.
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
        client.table("consultant_scorecard_raw").delete().gte("id", 0).execute()

    raw_count = 0
    for batch in chunked(raw_rows, size=100):
        client.table("consultant_scorecard_raw").insert(batch).execute()
        raw_count += len(batch)

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
        "consultant_scorecard_raw": raw_count,
        "consultant_scorecard_monthly": monthly_count,
        "consultant_scorecard_metric": metric_count,
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

