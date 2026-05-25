from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .files import read_consultant_scorecard_rows, read_salesforce_rows, read_tableau_rows


def chunked(items: list[dict[str, Any]], size: int = 100) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def ingest_rows(client: Any, table_name: str, rows: list[dict[str, Any]], replace_existing: bool = True) -> int:
    if not rows:
        return 0

    if replace_existing:
        client.table(table_name).delete().gte("id", 0).execute()

    inserted = 0
    for batch in chunked(rows, size=100):
        client.table(table_name).insert(batch).execute()
        inserted += len(batch)
    return inserted


def ingest_all_sources(client: Any, salesforce_path: str, tableau_path: str, scorecard_path: str, replace_existing: bool = True) -> dict[str, int]:
    salesforce_rows = read_salesforce_rows(salesforce_path)
    tableau_rows = read_tableau_rows(tableau_path)
    scorecard_rows = read_consultant_scorecard_rows(scorecard_path)

    return {
        "salesforce_data": ingest_rows(client, "salesforce_data", salesforce_rows, replace_existing=replace_existing),
        "tableau_data": ingest_rows(client, "tableau_data", tableau_rows, replace_existing=replace_existing),
        "consultant_scorecard_data": ingest_rows(client, "consultant_scorecard_data", scorecard_rows, replace_existing=replace_existing),
    }

