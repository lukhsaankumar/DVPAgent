from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import difflib
import logging

from simple_salesforce import Salesforce
from simple_salesforce.exceptions import SalesforceResourceNotFound

from ..config import SalesforceConfig
from .client import with_retries
from .errors import SalesforceMetadataError

logger = logging.getLogger("dvp_meeting_prep.salesforce")

# Relationship name -> the lookup field that must exist for a dotted
# projection like "Owner.Name" to be safe to include in a SELECT.
KNOWN_RELATIONSHIP_ROOT_FIELDS: dict[str, str] = {
    "Owner": "OwnerId",
    "Who": "WhoId",
    "What": "WhatId",
    "Account": "AccountId",
    "CreatedBy": "CreatedById",
    "LastModifiedBy": "LastModifiedById",
}

DISCOVERY_KEYWORDS = ("advisor", "practice", "number", "wholesaler", "territory")


def global_describe(client: Salesforce) -> list[dict[str, Any]]:
    result = with_retries(lambda: client.describe(), step_name="global_describe")
    return list((result or {}).get("sobjects", []))


def describe_object(client: Salesforce, object_name: str) -> dict[str, Any]:
    """Describe a single sObject. Raises SalesforceMetadataError if it doesn't exist or isn't queryable."""
    try:
        sf_type = getattr(client, object_name)
        described = with_retries(lambda: sf_type.describe(), step_name=f"describe_{object_name}")
    except SalesforceResourceNotFound as exc:
        raise SalesforceMetadataError(f"Salesforce object {object_name!r} does not exist or is not accessible.") from exc
    if not described or not described.get("queryable", True):
        raise SalesforceMetadataError(f"Salesforce object {object_name!r} exists but is not queryable by this user.")
    return described


def describe_field_names(described: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["name"]: f for f in described.get("fields", [])}


def suggest_field_name(target: str, candidates: list[str]) -> str | None:
    matches = difflib.get_close_matches(target, candidates, n=1, cutoff=0.6)
    return matches[0] if matches else None


def _is_valid_relationship_projection(described: dict[str, Any], projection: str) -> bool:
    relationship, _, leaf = projection.partition(".")
    if not leaf:
        return False
    root_field = KNOWN_RELATIONSHIP_ROOT_FIELDS.get(relationship)
    if root_field is None:
        return False
    return root_field in describe_field_names(described)


@dataclass
class FieldSelection:
    available: list[str] = field(default_factory=list)
    omitted: list[tuple[str, str]] = field(default_factory=list)


def select_available_fields(described: dict[str, Any], preferred: list[str]) -> FieldSelection:
    """Intersect a preferred field list with what actually exists on the object.

    Note on "queryable"/"accessible": Salesforce's describe API does not
    expose a clean per-field "the running user can query this" flag the way
    it exposes createable/updateable for DML -- a field either appears in
    describe() (queryable in SELECT) or it doesn't. True field-level-security
    denials on read surface as either the field being absent here or a null
    value at query time, not a separate boolean, so "exists in describe" is
    used as the queryable/accessible signal.
    """
    fields_by_name = describe_field_names(described)
    selection = FieldSelection()
    seen: set[str] = set()
    for name in preferred:
        name = (name or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        if "." in name:
            if _is_valid_relationship_projection(described, name):
                selection.available.append(name)
            else:
                selection.omitted.append((name, "relationship root lookup field not found on object"))
            continue
        if name in fields_by_name:
            selection.available.append(name)
        else:
            suggestion = suggest_field_name(name, list(fields_by_name.keys()))
            reason = "field not found on object"
            if suggestion:
                reason += f"; did you mean {suggestion!r}?"
            selection.omitted.append((name, reason))
    return selection


def _object_matches_keywords(sobject: dict[str, Any]) -> bool:
    haystack = f"{sobject.get('name', '')} {sobject.get('label', '')}".lower()
    return any(keyword in haystack for keyword in DISCOVERY_KEYWORDS)


def _field_matches_keywords(field_meta: dict[str, Any]) -> bool:
    haystack = f"{field_meta.get('name', '')} {field_meta.get('label', '')}".lower()
    return any(keyword in haystack for keyword in DISCOVERY_KEYWORDS)


def run_discovery(client: Salesforce, sf_config: SalesforceConfig) -> None:
    """Print/log a metadata discovery report to help find real object/field API names.

    Mirrors `python scripts/salesforce_extract.py --discover-salesforce`.
    Never raises for a missing/misconfigured field -- discovery's whole job
    is to surface exactly that, so it always runs to completion.
    """
    print("[METADATA] Validating configured objects and fields")
    logger.info("[METADATA] Running Salesforce metadata discovery")

    print("\n[METADATA] Global describe: objects whose name/label look advisor/practice-related")
    global_objects = global_describe(client)
    candidates = [o for o in global_objects if _object_matches_keywords(o)]
    if candidates:
        for obj in candidates[:25]:
            print(f"  - {obj.get('name')} ({obj.get('label')}) queryable={obj.get('queryable')}")
    else:
        print("  (none found by keyword match -- the object may use unrelated naming)")

    objects_to_describe = {sf_config.advisor_object, "Task", "Opportunity", "Account", "Contact"}
    described_by_object: dict[str, dict[str, Any]] = {}
    for object_name in sorted(objects_to_describe):
        print(f"\n[METADATA] Describing {object_name}")
        try:
            described = describe_object(client, object_name)
        except SalesforceMetadataError as exc:
            print(f"  NOT AVAILABLE: {exc}")
            continue
        described_by_object[object_name] = described

        matching_fields = [f for f in described.get("fields", []) if _field_matches_keywords(f)]
        if matching_fields:
            print("  Fields matching advisor/practice/number/wholesaler/territory:")
            for f in matching_fields:
                print(f"    - {f['name']} ({f.get('label')}, type={f.get('type')})")

        lookups = [f for f in described.get("fields", []) if f.get("type") == "reference" and f.get("referenceTo")]
        if lookups:
            print("  Lookup fields:")
            for f in lookups:
                print(f"    - {f['name']} -> {', '.join(f.get('referenceTo') or [])}")

    print("\n[METADATA] Validating configured field names")
    _report_field(described_by_object.get(sf_config.advisor_object), sf_config.advisor_object, "SF_ADVISOR_NUMBER_FIELD", sf_config.advisor_number_field)
    if sf_config.practice_lookup_field:
        _report_field(described_by_object.get(sf_config.advisor_object), sf_config.advisor_object, "SF_PRACTICE_LOOKUP_FIELD", sf_config.practice_lookup_field)
    else:
        print("  SF_PRACTICE_LOOKUP_FIELD: not set -- Advisor record IDs will be used directly as the relationship scope.")
    _report_field(described_by_object.get("Task"), "Task", "SF_TASK_LINK_FIELD", sf_config.task_link_field)
    _report_field(described_by_object.get("Opportunity"), "Opportunity", "SF_OPPORTUNITY_LINK_FIELD", sf_config.opportunity_link_field)

    for legacy_column, target in sf_config.advisor_field_map.items():
        _report_field(described_by_object.get(sf_config.advisor_object), sf_config.advisor_object, f"SF_ADVISOR_FIELD_MAP[{legacy_column}]", target)
    for legacy_column, target in sf_config.task_field_map.items():
        _report_field(described_by_object.get("Task"), "Task", f"SF_TASK_FIELD_MAP[{legacy_column}]", target)

    for extra in sf_config.advisor_extra_fields:
        _report_field(described_by_object.get(sf_config.advisor_object), sf_config.advisor_object, "SF_ADVISOR_EXTRA_FIELDS", extra)
    for extra in sf_config.task_extra_fields:
        _report_field(described_by_object.get("Task"), "Task", "SF_TASK_EXTRA_FIELDS", extra)
    for extra in sf_config.opportunity_extra_fields:
        _report_field(described_by_object.get("Opportunity"), "Opportunity", "SF_OPPORTUNITY_EXTRA_FIELDS", extra)

    print("\n[METADATA] Configuration validation complete")


def _report_field(described: dict[str, Any] | None, object_name: str, config_key: str, field_name: str) -> None:
    if described is None:
        print(f"  {config_key}={field_name!r}: SKIPPED ({object_name} could not be described)")
        return
    selection = select_available_fields(described, [field_name])
    if selection.available:
        print(f"  {config_key}={field_name!r}: OK on {object_name}")
    else:
        _, reason = selection.omitted[0]
        print(f"  {config_key}={field_name!r}: MISSING on {object_name} ({reason})")
