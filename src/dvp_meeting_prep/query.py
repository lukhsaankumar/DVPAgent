from __future__ import annotations

from typing import Any


SOURCE_TABLES = (
    ("salesforce_data", "advisor_name"),
    ("tableau_data", "advisor_name"),
)


def fetch_rows_for_advisor(client: Any, table_name: str, advisor_name: str, column_name: str = "advisor_name") -> list[dict[str, Any]]:
    response = client.table(table_name).select("*").eq(column_name, advisor_name).order("ingested_at", desc=True).execute()
    return response.data or []


def fetch_all_sources_for_advisor(client: Any, advisor_name: str) -> dict[str, list[dict[str, Any]]]:
    base_results = {
        table_name: fetch_rows_for_advisor(client, table_name, advisor_name, column_name)
        for table_name, column_name in SOURCE_TABLES
    }
    base_results.update(fetch_consultant_scorecard_for_advisor(client, advisor_name))
    return base_results


def _advisor_name_variants(advisor_name: str) -> list[str]:
    name = advisor_name.strip()
    if not name:
        return []
    variants = [name]
    parts = name.split()
    if len(parts) >= 2:
        first = parts[0]
        last = " ".join(parts[1:])
        variants.append(f"{last.upper()}, {first.upper()}")
    dedup: list[str] = []
    for value in variants:
        if value not in dedup:
            dedup.append(value)
    return dedup


def _advisor_numbers_from_other_sources(client: Any, advisor_name: str) -> list[str]:
    numbers: list[str] = []
    sf_rows = fetch_rows_for_advisor(client, "salesforce_data", advisor_name, "advisor_name")
    for row in sf_rows:
        value = row.get("advisor_number")
        if value is not None:
            text = str(value)
            if text not in numbers:
                numbers.append(text)

    tb_rows = fetch_rows_for_advisor(client, "tableau_data", advisor_name, "advisor_name")
    for row in tb_rows:
        name_number = row.get("advisor_name_number")
        if not name_number:
            continue
        text = str(name_number)
        if "-" in text:
            maybe_number = text.split("-")[-1].strip()
            if maybe_number and maybe_number not in numbers:
                numbers.append(maybe_number)
    return numbers


def fetch_consultant_scorecard_for_advisor(client: Any, advisor_name: str) -> dict[str, list[dict[str, Any]]]:
    monthly_rows: list[dict[str, Any]] = []
    for variant in _advisor_name_variants(advisor_name):
        monthly_rows = fetch_rows_for_advisor(client, "consultant_scorecard_monthly", variant, "advisor_name")
        if monthly_rows:
            break

    if not monthly_rows:
        for advisor_number in _advisor_numbers_from_other_sources(client, advisor_name):
            monthly_rows = fetch_rows_for_advisor(client, "consultant_scorecard_monthly", advisor_number, "advisor_number")
            if monthly_rows:
                break

    metric_rows: list[dict[str, Any]] = []
    scorecard_ids = [row.get("id") for row in monthly_rows if row.get("id") is not None]
    for scorecard_id in scorecard_ids:
        rows = client.table("consultant_scorecard_metric").select("*").eq("scorecard_id", scorecard_id).execute().data or []
        metric_rows.extend(rows)

    return {
        "consultant_scorecard_monthly": monthly_rows,
        "consultant_scorecard_metric": metric_rows,
    }

