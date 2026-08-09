from __future__ import annotations

import time
from typing import Any

# Advisor identity lives in these two source tables (the ones that use plain
# "First Last" naming). The consultant scorecard stores "LAST, FIRST" and is
# matched via query.fetch_consultant_scorecard_for_advisor's name variants,
# so it is intentionally not a source of truth for the searchable list.
ADVISOR_SOURCE_TABLES = ("salesforce_data", "tableau_data")

PAGE_SIZE = 1000
CACHE_TTL_SECONDS = 30

_cache: dict[str, Any] = {"names": None, "fetched_at": 0.0}


def _fetch_distinct_advisor_names(client: Any, table_name: str) -> set[str]:
    names: set[str] = set()
    start = 0
    while True:
        end = start + PAGE_SIZE - 1
        response = client.table(table_name).select("advisor_name").range(start, end).execute()
        rows = response.data or []
        for row in rows:
            name = row.get("advisor_name")
            if name and str(name).strip():
                names.add(str(name).strip())
        if len(rows) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return names


def _load_all_advisor_names(client: Any) -> list[str]:
    names: set[str] = set()
    for table_name in ADVISOR_SOURCE_TABLES:
        names.update(_fetch_distinct_advisor_names(client, table_name))
    return sorted(names, key=str.casefold)


def list_advisor_names(client: Any, *, force_refresh: bool = False) -> list[str]:
    now = time.monotonic()
    is_stale = _cache["names"] is None or (now - _cache["fetched_at"]) > CACHE_TTL_SECONDS
    if force_refresh or is_stale:
        _cache["names"] = _load_all_advisor_names(client)
        _cache["fetched_at"] = now
    return list(_cache["names"])


def search_advisor_names(client: Any, prefix: str, limit: int = 20) -> list[str]:
    prefix_normalized = prefix.strip().casefold()
    if not prefix_normalized:
        return []
    matches = [name for name in list_advisor_names(client) if name.casefold().startswith(prefix_normalized)]
    return matches[:limit]
