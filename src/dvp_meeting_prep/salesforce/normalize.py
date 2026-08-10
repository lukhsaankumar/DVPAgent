from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic
from typing import Any
import logging

from ..config import SalesforceConfig
from .queries import ScopeResolution

logger = logging.getLogger("dvp_meeting_prep.salesforce")

# The exact row shape src/dvp_meeting_prep/files.py:read_salesforce_rows()
# produces from the Excel-based source. This is the contract downstream code
# (ingest.py -> the salesforce_data table -> prompting.py) depends on -- see
# SALESFORCE_KEYS in prompting.py for the subset actually used in the LLM
# prompt. Every key here must exist in every returned row.
LEGACY_ROW_COLUMNS = [
    "advisor_name",
    "advisor_number",
    "task_subtype",
    "subject",
    "comments",
    "interaction_type",
    "completed_date_time",
    "district_vp_wholesaling",
    "pwm",
    "book_size",
    "assets_under_management",
    "new_business_ytd",
    "created_date",
    "start_date",
    "status",
    "area",
    "region_office_number",
    "assigned",
    "raw_payload",
]


def strip_attributes(value: Any) -> Any:
    """Recursively remove Salesforce REST metadata (`attributes`: type/url)."""
    if isinstance(value, dict):
        return {key: strip_attributes(v) for key, v in value.items() if key != "attributes"}
    if isinstance(value, list):
        return [strip_attributes(v) for v in value]
    return value


def normalize_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [strip_attributes(r) for r in records]


def find_duplicate_ids(records: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for record in records:
        record_id = record.get("Id")
        if record_id is None:
            continue
        if record_id in seen:
            duplicates.append(record_id)
        seen.add(record_id)
    return duplicates


def _get_dotted(record: dict[str, Any], path: str) -> Any:
    current: Any = record
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _mapped(record: dict[str, Any], field_map: dict[str, str], legacy_key: str, default_field: str | None = None) -> Any:
    target = field_map.get(legacy_key, default_field)
    if not target:
        return None
    return _get_dotted(record, target)


def _build_legacy_row(advisor: dict[str, Any], task: dict[str, Any], sf_config: SalesforceConfig) -> dict[str, Any]:
    advisor_number = advisor.get(sf_config.advisor_number_field)
    return {
        "advisor_name": advisor.get("Name"),
        "advisor_number": str(advisor_number).strip() if advisor_number is not None else None,
        "task_subtype": _mapped(task, sf_config.task_field_map, "task_subtype", "TaskSubtype"),
        "subject": _mapped(task, sf_config.task_field_map, "subject", "Subject"),
        "comments": _mapped(task, sf_config.task_field_map, "comments", "Description"),
        "interaction_type": _mapped(task, sf_config.task_field_map, "interaction_type", "Type"),
        "completed_date_time": _mapped(task, sf_config.task_field_map, "completed_date_time", "CompletedDateTime"),
        "district_vp_wholesaling": _mapped(advisor, sf_config.advisor_field_map, "district_vp_wholesaling"),
        "pwm": _mapped(advisor, sf_config.advisor_field_map, "pwm"),
        "book_size": _mapped(advisor, sf_config.advisor_field_map, "book_size"),
        "assets_under_management": _mapped(advisor, sf_config.advisor_field_map, "assets_under_management"),
        "new_business_ytd": _mapped(advisor, sf_config.advisor_field_map, "new_business_ytd"),
        "created_date": _mapped(task, sf_config.task_field_map, "created_date", "CreatedDate"),
        "start_date": _mapped(advisor, sf_config.advisor_field_map, "start_date"),
        "status": _mapped(task, sf_config.task_field_map, "status", "Status"),
        "area": _mapped(advisor, sf_config.advisor_field_map, "area"),
        "region_office_number": _mapped(advisor, sf_config.advisor_field_map, "region_office_number"),
        "assigned": _mapped(advisor, sf_config.advisor_field_map, "assigned"),
        "raw_payload": {"advisor": strip_attributes(advisor), "task": strip_attributes(task)},
    }


@dataclass
class NormalizationResult:
    legacy_rows: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[dict[str, str]] = field(default_factory=list)
    advisor_to_task_matches: int = 0
    duplicate_task_ids: list[str] = field(default_factory=list)
    duplicate_advisor_ids: list[str] = field(default_factory=list)
    unmatched_scope_ids: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0


def build_legacy_rows(
    advisor_records: list[dict[str, Any]],
    task_records: list[dict[str, Any]],
    scope: ScopeResolution,
    sf_config: SalesforceConfig,
) -> NormalizationResult:
    """Join Tasks back to their Advisor and produce the same denormalized row
    shape the legacy Excel-based source produced (one row per Task).

    A Task whose link-field value doesn't match any resolved scope ID can't
    be attached to an advisor_name/advisor_number (both NOT NULL in the
    salesforce_data table), so it's reported in `dropped` rather than
    silently discarded or inserted with nulls.
    """
    start = monotonic()
    result = NormalizationResult()
    result.duplicate_task_ids = find_duplicate_ids(task_records)
    result.duplicate_advisor_ids = find_duplicate_ids(advisor_records)

    scope_id_to_numbers: dict[str, list[str]] = {}
    for number, scope_id in scope.number_to_scope_id.items():
        scope_id_to_numbers.setdefault(scope_id, []).append(number)

    unmatched_scope_ids: set[str] = set()
    for task in task_records:
        link_value = task.get(sf_config.task_link_field)
        task_id = task.get("Id")
        if not link_value:
            result.dropped.append({"id": str(task_id), "reason": f"Task has no value in link field {sf_config.task_link_field!r}"})
            continue

        matching_numbers = scope_id_to_numbers.get(link_value)
        if not matching_numbers:
            unmatched_scope_ids.add(link_value)
            result.dropped.append({"id": str(task_id), "reason": f"Task link field value {link_value!r} does not match any resolved advisor/practice scope ID"})
            continue

        for number in matching_numbers:
            advisor = scope.number_to_advisor.get(number)
            if advisor is None:
                continue
            result.legacy_rows.append(_build_legacy_row(advisor, task, sf_config))
            result.advisor_to_task_matches += 1

    result.unmatched_scope_ids = sorted(unmatched_scope_ids)
    result.duration_seconds = monotonic() - start

    logger.info("[NORMALIZE] raw_advisor_rows=%d raw_task_rows=%d", len(advisor_records), len(task_records))
    logger.info(
        "[JOIN] advisor_to_task_matches=%d unmatched_scope_ids=%d dropped=%d duration=%.2fs",
        result.advisor_to_task_matches,
        len(result.unmatched_scope_ids),
        len(result.dropped),
        result.duration_seconds,
    )
    if result.dropped:
        logger.warning("[JOIN] dropped rows (id, reason): %s", result.dropped[:10])
    if result.duplicate_task_ids:
        logger.warning("[JOIN] duplicate Task IDs detected: %s", result.duplicate_task_ids)
    if result.duplicate_advisor_ids:
        logger.warning("[JOIN] duplicate Advisor IDs detected: %s", result.duplicate_advisor_ids)
    logger.info("[NORMALIZE] output_columns=%s output_row_count=%d", LEGACY_ROW_COLUMNS, len(result.legacy_rows))

    return result


def match_opportunities_to_scope(
    opportunity_records: list[dict[str, Any]],
    scope: ScopeResolution,
    sf_config: SalesforceConfig,
) -> tuple[int, list[str]]:
    """Count how many Opportunities matched a resolved scope ID, and which
    Opportunity scope-id values didn't match anything (diagnostic only --
    Opportunities aren't part of the legacy row contract, see
    docs/SALESFORCE_SETUP.md for why).
    """
    known_scope_ids = set(scope.number_to_scope_id.values())
    matched = 0
    unmatched: set[str] = set()
    for opportunity in opportunity_records:
        link_value = opportunity.get(sf_config.opportunity_link_field)
        if link_value in known_scope_ids:
            matched += 1
        elif link_value:
            unmatched.add(link_value)
    return matched, sorted(unmatched)
