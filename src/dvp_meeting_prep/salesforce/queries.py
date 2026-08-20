from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any
import re
import logging

from simple_salesforce import Salesforce, format_soql

from ..config import SalesforceConfig
from .client import with_retries
from .errors import SalesforceMetadataError, SalesforceQueryError
from .metadata import describe_field_names, select_available_fields
from .timing import timed_step

logger = logging.getLogger("dvp_meeting_prep.salesforce")

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")

ADVISOR_BASE_FIELDS = ["Id", "Name", "OwnerId", "Owner.Name", "CreatedDate", "LastModifiedDate"]
TASK_BASE_FIELDS = [
    "Id",
    "Subject",
    "ActivityDate",
    "CompletedDateTime",
    "Status",
    "Priority",
    "IsClosed",
    "IsArchived",
    "TaskSubtype",
    "Description",
    "WhoId",
    "WhatId",
    "AccountId",
    "OwnerId",
    "CreatedById",
    "CreatedDate",
    "LastModifiedDate",
    "SystemModstamp",
]
OPPORTUNITY_BASE_FIELDS = [
    "Id",
    "Name",
    "StageName",
    "Amount",
    "Probability",
    "CloseDate",
    "Type",
    "LeadSource",
    "NextStep",
    "Description",
    "IsClosed",
    "IsWon",
    "OwnerId",
    "CreatedDate",
    "LastModifiedDate",
    "SystemModstamp",
]


def _validate_identifier(name: str) -> str:
    """Guard against a misconfigured object/field name being used as a raw
    (unquotable) SOQL identifier. Values go through format_soql for escaping;
    identifiers can't be quoted as SOQL string literals, so they're validated
    against an allow-list pattern instead.
    """
    if not _IDENTIFIER_RE.match(name):
        raise SalesforceMetadataError(
            f"{name!r} is not a valid Salesforce API name (object/field names must be "
            "alphanumeric/underscore, optionally dotted for relationships)."
        )
    return name


class MalformedSalesforceQueryError(SalesforceMetadataError):
    """Raised when a query would be structurally invalid (e.g. an empty IN list)."""


def _require_non_empty(values: list[str], what: str) -> None:
    if not values:
        raise MalformedSalesforceQueryError(f"Cannot query with an empty {what} list -- SOQL 'IN ()' is invalid.")


def build_advisor_query(described: dict[str, Any], sf_config: SalesforceConfig) -> tuple[str, list[str]]:
    """Returns (soql, omitted_field_names) for the advisor/practice lookup query.

    Which advisors get selected is controlled by advisor_lookup_field /
    advisor_lookup_values (defaulting to advisor_number_field / advisor_numbers
    when SF_ADVISOR_LOOKUP_FIELD isn't set -- unchanged behavior). This split
    exists because advisor_number_field isn't guaranteed to be populated for
    every real advisor record in every org; when it isn't, pointing
    SF_ADVISOR_LOOKUP_FIELD at a field that reliably *is* populated (e.g.
    "Name") is how selection still works. advisor_number_field is still
    requested in the SELECT list (best-effort, to populate the advisor_number
    output column) but is not required to exist -- only advisor_lookup_field is.
    """
    preferred = ["Id", "Name", sf_config.advisor_number_field, sf_config.advisor_lookup_field]
    if sf_config.practice_lookup_field:
        preferred.append(sf_config.practice_lookup_field)
    preferred += ADVISOR_BASE_FIELDS
    preferred += list(sf_config.advisor_field_map.values())
    preferred += list(sf_config.advisor_extra_fields)

    selection = select_available_fields(described, preferred)
    for omitted_field, reason in selection.omitted:
        logger.warning("[ADVISORS] Omitting preferred field %r: %s", omitted_field, reason)

    if sf_config.advisor_lookup_field not in selection.available:
        raise SalesforceMetadataError(
            f"SF_ADVISOR_LOOKUP_FIELD={sf_config.advisor_lookup_field!r} does not exist on "
            f"{sf_config.advisor_object}. Run --discover-salesforce to find the correct field name."
        )
    _require_non_empty(list(sf_config.advisor_lookup_values), "SF_ADVISOR_LOOKUP_VALUES/SF_ADVISOR_NUMBERS")

    field_list = ", ".join(_validate_identifier(f) for f in selection.available)
    object_name = _validate_identifier(sf_config.advisor_object)
    lookup_field = _validate_identifier(sf_config.advisor_lookup_field)

    soql = format_soql(
        f"SELECT {field_list} FROM {object_name} WHERE {lookup_field} IN {{values}}",
        values=[v.strip() for v in sf_config.advisor_lookup_values],
    )
    return soql, [name for name, _ in selection.omitted]


def fetch_advisors(client: Salesforce, described: dict[str, Any], sf_config: SalesforceConfig) -> list[dict[str, Any]]:
    soql, _omitted = build_advisor_query(described, sf_config)
    logger.debug("[ADVISORS] SOQL: %s", soql)
    with timed_step("ADVISORS", echo=True) as timer:
        print("[ADVISORS] Querying Advisor records")
        result = with_retries(lambda: client.query_all(soql), step_name="fetch_advisors")
        records = list(result.get("records", []))
    print(f"[ADVISORS] Retrieved {len(records)} records")
    logger.info(
        "[ADVISORS] requested=%d returned=%d duration=%.2fs",
        len(sf_config.advisor_numbers),
        len(records),
        timer.duration_seconds,
    )
    return records


def build_all_advisors_query(described: dict[str, Any], sf_config: SalesforceConfig) -> tuple[str, list[str]]:
    """Returns (soql, omitted_field_names) for an UNSCOPED advisor query --
    every record with a populated advisor_number_field, not just the
    specific SF_ADVISOR_NUMBERS list.

    Only for discovery/comparison tooling (scripts/compare_salesforce_sources.py)
    where the real advisor number values aren't known ahead of time yet.
    The normal build_advisor_query() above stays scoped to SF_ADVISOR_NUMBERS
    for the actual application ingest path, which must not silently pull
    every record of the advisor object in the org.
    """
    preferred = ["Id", "Name", sf_config.advisor_number_field]
    if sf_config.practice_lookup_field:
        preferred.append(sf_config.practice_lookup_field)
    preferred += ADVISOR_BASE_FIELDS
    preferred += list(sf_config.advisor_field_map.values())
    preferred += list(sf_config.advisor_extra_fields)

    selection = select_available_fields(described, preferred)
    for omitted_field, reason in selection.omitted:
        logger.warning("[ADVISORS-ALL] Omitting preferred field %r: %s", omitted_field, reason)

    if sf_config.advisor_number_field not in selection.available:
        raise SalesforceMetadataError(
            f"SF_ADVISOR_NUMBER_FIELD={sf_config.advisor_number_field!r} does not exist on "
            f"{sf_config.advisor_object}. Run --discover-salesforce to find the correct field name."
        )

    field_list = ", ".join(_validate_identifier(f) for f in selection.available)
    object_name = _validate_identifier(sf_config.advisor_object)
    number_field = _validate_identifier(sf_config.advisor_number_field)

    soql = f"SELECT {field_list} FROM {object_name} WHERE {number_field} != null"
    return soql, [name for name, _ in selection.omitted]


def fetch_all_advisors(client: Salesforce, described: dict[str, Any], sf_config: SalesforceConfig) -> list[dict[str, Any]]:
    """Unscoped pull of every advisor record with a populated advisor
    number -- not limited to SF_ADVISOR_NUMBERS. Discovery/comparison
    tooling only; see build_all_advisors_query()."""
    soql, _omitted = build_all_advisors_query(described, sf_config)
    logger.debug("[ADVISORS-ALL] SOQL: %s", soql)
    with timed_step("ADVISORS-ALL", echo=True) as timer:
        print("[ADVISORS-ALL] Querying every advisor record with a populated advisor number (unscoped)")
        result = with_retries(lambda: client.query_all(soql), step_name="fetch_all_advisors")
        records = list(result.get("records", []))
    print(f"[ADVISORS-ALL] Retrieved {len(records)} records")
    logger.info("[ADVISORS-ALL] returned=%d duration=%.2fs", len(records), timer.duration_seconds)
    return records


@dataclass
class ScopeResolution:
    number_to_advisor: dict[str, dict[str, Any]] = field(default_factory=dict)
    number_to_scope_id: dict[str, str] = field(default_factory=dict)
    missing_numbers: list[str] = field(default_factory=list)
    duplicate_numbers: list[str] = field(default_factory=list)
    scope_ids: list[str] = field(default_factory=list)


def resolve_scope(advisor_records: list[dict[str, Any]], sf_config: SalesforceConfig) -> ScopeResolution:
    """Map each configured advisor lookup value (advisor number, or Name when
    SF_ADVISOR_LOOKUP_FIELD=Name) to its Salesforce record and the
    relationship scope ID used to query Tasks/Opportunities (the Practice
    lookup target when configured, otherwise the Advisor record ID itself).

    Matching against the requested list is case-insensitive: when the lookup
    field is Name, "Scott Syrja" (as configured) and "SCOTT SYRJA" (as
    returned by Salesforce, or vice versa) must resolve to the same advisor,
    same as the case-insensitive matching already used elsewhere in this
    codebase for cross-source advisor-name joins.
    """
    resolution = ScopeResolution()
    seen_numbers: dict[str, int] = {}
    casefold_to_key: dict[str, str] = {}

    for record in advisor_records:
        number = record.get(sf_config.advisor_lookup_field)
        if number is None:
            continue
        number = str(number).strip()
        seen_numbers[number] = seen_numbers.get(number, 0) + 1
        if number in resolution.number_to_advisor:
            continue
        resolution.number_to_advisor[number] = record
        casefold_to_key[number.casefold()] = number

        if sf_config.practice_lookup_field:
            scope_id = record.get(sf_config.practice_lookup_field)
        else:
            scope_id = record.get("Id")
        if scope_id:
            resolution.number_to_scope_id[number] = scope_id

    requested = [n.strip() for n in sf_config.advisor_lookup_values]
    resolution.missing_numbers = [n for n in requested if n.casefold() not in casefold_to_key]
    resolution.duplicate_numbers = [n for n, count in seen_numbers.items() if count > 1]
    resolution.scope_ids = sorted(set(resolution.number_to_scope_id.values()))

    logger.info(
        "[ADVISORS] found=%d missing=%s duplicates=%s unique_scope_ids=%d",
        len(resolution.number_to_advisor),
        resolution.missing_numbers,
        resolution.duplicate_numbers,
        len(resolution.scope_ids),
    )
    return resolution


def audit_task_subjects(client: Salesforce, scope_ids: list[str], sf_config: SalesforceConfig) -> list[dict[str, Any]]:
    """Debug/discovery-only aggregate query: Task subjects present for the resolved scope."""
    if not scope_ids:
        return []
    link_field = _validate_identifier(sf_config.task_link_field)
    soql = format_soql(
        "SELECT Subject, COUNT(Id) cnt FROM Task WHERE IsDeleted = FALSE AND "
        + link_field
        + " IN {scope_ids} GROUP BY Subject ORDER BY COUNT(Id) DESC",
        scope_ids=scope_ids,
    )
    logger.debug("[TASKS] Subject audit SOQL: %s", soql)
    result = with_retries(lambda: client.query(soql), step_name="audit_task_subjects")
    rows = list(result.get("records", []))
    for row in rows:
        logger.info("[TASKS] subject audit: %s = %s", row.get("Subject"), row.get("cnt"))
    return rows


def build_task_query(described: dict[str, Any], scope_ids: list[str], sf_config: SalesforceConfig) -> tuple[str, list[str]]:
    preferred = list(TASK_BASE_FIELDS) + list(sf_config.task_field_map.values()) + list(sf_config.task_extra_fields)
    selection = select_available_fields(described, preferred)
    for omitted_field, reason in selection.omitted:
        logger.warning("[TASKS] Omitting preferred field %r: %s", omitted_field, reason)

    fields_by_name = describe_field_names(described)
    if sf_config.task_link_field not in fields_by_name:
        raise SalesforceMetadataError(
            f"SF_TASK_LINK_FIELD={sf_config.task_link_field!r} does not exist on Task. "
            "Run --discover-salesforce to find the correct field name."
        )
    if sf_config.task_link_field not in selection.available:
        selection.available.append(sf_config.task_link_field)

    _require_non_empty(scope_ids, "advisor/practice scope")
    _require_non_empty(list(sf_config.task_subjects), "SF_TASK_SUBJECTS")

    field_list = ", ".join(_validate_identifier(f) for f in selection.available)
    link_field = _validate_identifier(sf_config.task_link_field)

    where_parts = [
        "IsDeleted = FALSE",
        link_field + " IN {scope_ids}",
        "Subject IN {subjects}",
    ]
    kwargs: dict[str, Any] = {"scope_ids": scope_ids, "subjects": list(sf_config.task_subjects)}
    if sf_config.activity_start_date:
        where_parts.append("ActivityDate >= {start_date}")
        kwargs["start_date"] = date.fromisoformat(sf_config.activity_start_date)

    where_clause = " AND ".join(where_parts)
    soql = format_soql(f"SELECT {field_list} FROM Task WHERE {where_clause}", **kwargs)
    return soql, [name for name, _ in selection.omitted]


def fetch_tasks(client: Salesforce, described: dict[str, Any], scope_ids: list[str], sf_config: SalesforceConfig) -> list[dict[str, Any]]:
    soql, _omitted = build_task_query(described, scope_ids, sf_config)
    logger.info(
        "[TASKS] link_field=%s scope_ids=%d subjects=%s start_date_active=%s query_all=True",
        sf_config.task_link_field,
        len(scope_ids),
        list(sf_config.task_subjects),
        bool(sf_config.activity_start_date),
    )
    logger.debug("[TASKS] SOQL: %s", soql)
    with timed_step("TASKS", echo=True) as timer:
        print(f"[TASKS] Querying {'/'.join(sf_config.task_subjects)} Tasks with QueryAll")
        try:
            result = with_retries(lambda: client.query_all(soql, include_deleted=True), step_name="fetch_tasks")
        except Exception as exc:  # noqa: BLE001 -- re-raise as a typed, descriptive error
            raise SalesforceQueryError(f"Task query failed: {exc}") from exc
        records = list(result.get("records", []))
    print(f"[TASKS] Retrieved {len(records)} records")
    _log_task_summary(records, timer.duration_seconds)
    return records


def _log_task_summary(records: list[dict[str, Any]], duration_seconds: float) -> None:
    subject_counts: dict[str, int] = {}
    activity_dates = [r.get("ActivityDate") for r in records if r.get("ActivityDate")]
    blank_description = sum(1 for r in records if not r.get("Description"))
    archived = sum(1 for r in records if r.get("IsArchived"))
    for r in records:
        subject = r.get("Subject") or "(blank)"
        subject_counts[subject] = subject_counts.get(subject, 0) + 1

    logger.info("[TASKS] duration=%.2fs returned=%d", duration_seconds, len(records))
    logger.info("[TASKS] count_by_subject=%s", subject_counts)
    logger.info(
        "[TASKS] min_activity_date=%s max_activity_date=%s blank_description=%d archived=%d",
        min(activity_dates, default=None),
        max(activity_dates, default=None),
        blank_description,
        archived,
    )
    sample = [{"Id": r.get("Id"), "Subject": r.get("Subject"), "ActivityDate": r.get("ActivityDate")} for r in records[:3]]
    logger.debug("[TASKS] sample=%s", sample)


def build_opportunity_query(described: dict[str, Any], scope_ids: list[str], sf_config: SalesforceConfig) -> tuple[str, list[str]]:
    preferred = ["Id", "Name", sf_config.opportunity_link_field] + list(OPPORTUNITY_BASE_FIELDS) + list(sf_config.opportunity_extra_fields)
    selection = select_available_fields(described, preferred)
    for omitted_field, reason in selection.omitted:
        logger.warning("[OPPORTUNITIES] Omitting preferred field %r: %s", omitted_field, reason)

    fields_by_name = describe_field_names(described)
    if sf_config.opportunity_link_field not in fields_by_name:
        raise SalesforceMetadataError(
            f"SF_OPPORTUNITY_LINK_FIELD={sf_config.opportunity_link_field!r} does not exist on Opportunity. "
            "Run --discover-salesforce to find the correct field name."
        )
    if sf_config.opportunity_link_field not in selection.available:
        selection.available.append(sf_config.opportunity_link_field)

    _require_non_empty(scope_ids, "advisor/practice scope")

    field_list = ", ".join(_validate_identifier(f) for f in selection.available)
    link_field = _validate_identifier(sf_config.opportunity_link_field)

    soql = format_soql(
        f"SELECT {field_list} FROM Opportunity WHERE {link_field} IN {{scope_ids}} ORDER BY IsClosed ASC, CloseDate ASC",
        scope_ids=scope_ids,
    )
    return soql, [name for name, _ in selection.omitted]


def fetch_opportunities(client: Salesforce, described: dict[str, Any], scope_ids: list[str], sf_config: SalesforceConfig) -> list[dict[str, Any]]:
    soql, _omitted = build_opportunity_query(described, scope_ids, sf_config)
    logger.debug("[OPPORTUNITIES] SOQL: %s", soql)
    with timed_step("OPPORTUNITIES", echo=True) as timer:
        print("[OPPORTUNITIES] Querying related Opportunities")
        result = with_retries(lambda: client.query_all(soql), step_name="fetch_opportunities")
        records = list(result.get("records", []))
    print(f"[OPPORTUNITIES] Retrieved {len(records)} records")
    _log_opportunity_summary(records, timer.duration_seconds)
    return records


def _log_opportunity_summary(records: list[dict[str, Any]], duration_seconds: float) -> None:
    stage_counts: dict[str, int] = {}
    for r in records:
        stage = r.get("StageName") or "(blank)"
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
    close_dates = [r.get("CloseDate") for r in records if r.get("CloseDate")]

    logger.info("[OPPORTUNITIES] duration=%.2fs returned=%d", duration_seconds, len(records))
    logger.info(
        "[OPPORTUNITIES] open=%d closed=%d won=%d count_by_stage=%s",
        sum(1 for r in records if not r.get("IsClosed")),
        sum(1 for r in records if r.get("IsClosed")),
        sum(1 for r in records if r.get("IsWon")),
        stage_counts,
    )
    logger.info(
        "[OPPORTUNITIES] null_amount=%d null_probability=%d earliest_close=%s latest_close=%s",
        sum(1 for r in records if r.get("Amount") is None),
        sum(1 for r in records if r.get("Probability") is None),
        min(close_dates, default=None),
        max(close_dates, default=None),
    )
    sample = [{"Id": r.get("Id"), "Name": r.get("Name"), "StageName": r.get("StageName"), "CloseDate": r.get("CloseDate")} for r in records[:3]]
    logger.debug("[OPPORTUNITIES] sample=%s", sample)
