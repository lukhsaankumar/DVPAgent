from __future__ import annotations

import time
from typing import Any

from .db import Database, fetch_all

# Advisor identity lives in these two source tables (the ones that use plain
# "First Last" naming). The consultant scorecard stores "LAST, FIRST" and is
# matched via query.fetch_consultant_scorecard_for_advisor's name variants,
# so it is intentionally not a source of truth for the searchable list.
ADVISOR_SOURCE_TABLES = ("salesforce_data", "tableau_data")

CACHE_TTL_SECONDS = 30

_cache: dict[str, Any] = {"names": None, "fetched_at": 0.0}


def _fetch_distinct_advisor_names(database: Database, table_name: str) -> set[str]:
    with database.read() as conn:
        rows = fetch_all(conn, f"SELECT DISTINCT advisor_name FROM {table_name}")
    names: set[str] = set()
    for row in rows:
        name = row.get("advisor_name")
        if name and str(name).strip():
            names.add(str(name).strip())
    return names


def _load_all_advisor_names(database: Database) -> list[str]:
    names: set[str] = set()
    for table_name in ADVISOR_SOURCE_TABLES:
        names.update(_fetch_distinct_advisor_names(database, table_name))
    return sorted(names, key=str.casefold)


def list_advisor_names(database: Database, *, force_refresh: bool = False) -> list[str]:
    now = time.monotonic()
    is_stale = _cache["names"] is None or (now - _cache["fetched_at"]) > CACHE_TTL_SECONDS
    if force_refresh or is_stale:
        _cache["names"] = _load_all_advisor_names(database)
        _cache["fetched_at"] = now
    return list(_cache["names"])


def search_advisor_names(database: Database, prefix: str, limit: int = 20) -> list[str]:
    prefix_normalized = prefix.strip().casefold()
    if not prefix_normalized:
        return []
    matches = [name for name in list_advisor_names(database) if name.casefold().startswith(prefix_normalized)]
    return matches[:limit]
