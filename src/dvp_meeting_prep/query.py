from __future__ import annotations

from typing import Any

from .db import Database, fetch_all, int_to_bool, json_loads

SOURCE_TABLES = (
    ("salesforce_data", "advisor_name"),
    ("tableau_data", "advisor_name"),
)

# Columns that need converting back from their SQLite storage representation
# (see db.py's JSON/boolean helpers and ingest.py's _prepare_row_for_insert).
_JSON_COLUMNS_BY_TABLE: dict[str, tuple[str, ...]] = {
    "salesforce_data": ("raw_payload",),
    "tableau_data": ("raw_payload",),
    "consultant_scorecard_monthly": ("raw_payload",),
}
_BOOLEAN_COLUMNS_BY_TABLE: dict[str, tuple[str, ...]] = {
    "consultant_scorecard_monthly": ("etf_completed_approved", "pwm_indicator"),
}


def _deserialize_row(table_name: str, row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for column in _JSON_COLUMNS_BY_TABLE.get(table_name, ()):
        if column in result:
            result[column] = json_loads(result[column])
    for column in _BOOLEAN_COLUMNS_BY_TABLE.get(table_name, ()):
        if column in result:
            result[column] = int_to_bool(result[column])
    return result


def fetch_rows_for_advisor(database: Database, table_name: str, advisor_name: str, column_name: str = "advisor_name") -> list[dict[str, Any]]:
    """`table_name`/`column_name` always come from hardcoded constants in this
    module, never from request input, so building the SQL text with them is
    safe -- SQL identifiers can't be bound as `?` parameters anyway.
    `advisor_name` (the one value that can come from a user/API caller) is
    always passed as a parameter.

    Matched case-insensitively: real Salesforce data stores "First Last"
    while real Tableau exports store "FIRST LAST" (all caps) -- an exact
    match would silently return zero rows for every Tableau lookup. The
    sample/dummy data used for earlier testing happened to use consistent
    casing across sources, which is why this didn't surface until testing
    against real data.
    """
    with database.read() as conn:
        rows = fetch_all(
            conn,
            f"SELECT * FROM {table_name} WHERE {column_name} = ? COLLATE NOCASE ORDER BY ingested_at DESC",
            (advisor_name,),
        )
    return [_deserialize_row(table_name, row) for row in rows]


def fetch_all_sources_for_advisor(database: Database, advisor_name: str) -> dict[str, list[dict[str, Any]]]:
    base_results = {
        table_name: fetch_rows_for_advisor(database, table_name, advisor_name, column_name)
        for table_name, column_name in SOURCE_TABLES
    }
    base_results.update(fetch_consultant_scorecard_for_advisor(database, advisor_name))
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


def _advisor_numbers_from_other_sources(database: Database, advisor_name: str) -> list[str]:
    numbers: list[str] = []
    sf_rows = fetch_rows_for_advisor(database, "salesforce_data", advisor_name, "advisor_name")
    for row in sf_rows:
        value = row.get("advisor_number")
        if value is not None:
            text = str(value)
            if text not in numbers:
                numbers.append(text)

    tb_rows = fetch_rows_for_advisor(database, "tableau_data", advisor_name, "advisor_name")
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


def fetch_consultant_scorecard_for_advisor(database: Database, advisor_name: str) -> dict[str, list[dict[str, Any]]]:
    monthly_rows: list[dict[str, Any]] = []
    for variant in _advisor_name_variants(advisor_name):
        monthly_rows = fetch_rows_for_advisor(database, "consultant_scorecard_monthly", variant, "advisor_name")
        if monthly_rows:
            break

    if not monthly_rows:
        for advisor_number in _advisor_numbers_from_other_sources(database, advisor_name):
            monthly_rows = fetch_rows_for_advisor(database, "consultant_scorecard_monthly", advisor_number, "advisor_number")
            if monthly_rows:
                break

    metric_rows: list[dict[str, Any]] = []
    scorecard_ids = [row.get("id") for row in monthly_rows if row.get("id") is not None]
    if scorecard_ids:
        placeholders = ", ".join(["?"] * len(scorecard_ids))
        with database.read() as conn:
            metric_rows = fetch_all(
                conn,
                f"SELECT * FROM consultant_scorecard_metric WHERE scorecard_id IN ({placeholders})",
                scorecard_ids,
            )

    return {
        "consultant_scorecard_monthly": monthly_rows,
        "consultant_scorecard_metric": metric_rows,
    }
