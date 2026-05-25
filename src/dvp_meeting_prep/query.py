from __future__ import annotations

from typing import Any


SOURCE_TABLES = (
    ("salesforce_data", "advisor_name"),
    ("tableau_data", "advisor_name"),
    ("consultant_scorecard_data", "advisor_name"),
)


def fetch_rows_for_advisor(client: Any, table_name: str, advisor_name: str, column_name: str = "advisor_name") -> list[dict[str, Any]]:
    response = client.table(table_name).select("*").eq(column_name, advisor_name).order("ingested_at", desc=True).execute()
    return response.data or []


def fetch_all_sources_for_advisor(client: Any, advisor_name: str) -> dict[str, list[dict[str, Any]]]:
    return {
        table_name: fetch_rows_for_advisor(client, table_name, advisor_name, column_name)
        for table_name, column_name in SOURCE_TABLES
    }

