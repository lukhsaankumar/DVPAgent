from __future__ import annotations

import time
from typing import Any

from .db import Database, fetch_all

# Advisor identity primarily comes from whichever Salesforce table is active
# (see db.SALESFORCE_TABLE_BY_ADVISOR_SOURCE_MODE -- "salesforce_data" for
# legacy/csv, "salesforce_data_auto" for a live extraction) plus Tableau --
# both use plain "First Last" naming. The consultant scorecard stores
# "LAST, FIRST" and is added as a supplemental source only, so an advisor
# with no Salesforce/Tableau data at all -- only a scorecard row -- can
# still show up in search.
TABLEAU_ADVISOR_TABLE = "tableau_data"
SCORECARD_ADVISOR_TABLE = "consultant_scorecard_monthly"

CACHE_TTL_SECONDS = 30

# Keyed by salesforce_table so switching which one is active (ADVISOR_SOURCE_MODE)
# never returns a stale list built from the other table.
_cache: dict[str, dict[str, Any]] = {}


def _fetch_distinct_advisor_names(database: Database, table_name: str) -> set[str]:
    with database.read() as conn:
        rows = fetch_all(conn, f"SELECT DISTINCT advisor_name FROM {table_name}")
    names: set[str] = set()
    for row in rows:
        name = row.get("advisor_name")
        if name and str(name).strip():
            names.add(str(name).strip())
    return names


def _load_all_advisor_names(database: Database, salesforce_table: str) -> list[str]:
    """Dedupe case-insensitively, not just exact-string: real Salesforce data
    stores "First Last" while real Tableau exports store "FIRST LAST" (all
    caps), so the same advisor's name differs in casing between sources. A
    plain set() union treats those as two different people and lists the
    same advisor twice. Source order (salesforce_table, then Tableau, then
    the scorecard) decides which casing/form wins when an advisor appears in
    more than one -- and is also what lets a scorecard-only advisor in.
    """
    seen_casefold: set[str] = set()
    names: list[str] = []
    for table_name in (salesforce_table, TABLEAU_ADVISOR_TABLE, SCORECARD_ADVISOR_TABLE):
        for name in sorted(_fetch_distinct_advisor_names(database, table_name)):
            key = name.casefold()
            if key in seen_casefold:
                continue
            seen_casefold.add(key)
            names.append(name)
    return sorted(names, key=str.casefold)


def list_advisor_names(database: Database, *, salesforce_table: str = "salesforce_data", force_refresh: bool = False) -> list[str]:
    cache_entry = _cache.setdefault(salesforce_table, {"names": None, "fetched_at": 0.0})
    now = time.monotonic()
    is_stale = cache_entry["names"] is None or (now - cache_entry["fetched_at"]) > CACHE_TTL_SECONDS
    if force_refresh or is_stale:
        cache_entry["names"] = _load_all_advisor_names(database, salesforce_table)
        cache_entry["fetched_at"] = now
    return list(cache_entry["names"])


def search_advisor_names(database: Database, prefix: str, *, salesforce_table: str = "salesforce_data", limit: int = 20) -> list[str]:
    prefix_normalized = prefix.strip().casefold()
    if not prefix_normalized:
        return []
    all_names = list_advisor_names(database, salesforce_table=salesforce_table)
    matches = [name for name in all_names if name.casefold().startswith(prefix_normalized)]
    return matches[:limit]
