from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from dvp_meeting_prep.config import SalesforceConfig
from dvp_meeting_prep.salesforce import queries as sf_queries
from dvp_meeting_prep.salesforce.errors import SalesforceMetadataError
from dvp_meeting_prep.salesforce.queries import MalformedSalesforceQueryError


def _sf_config(**overrides) -> SalesforceConfig:
    base = dict(
        auth_mode="password",
        username="user@example.com",
        password="hunter2",
        security_token="tok123",
        access_token=None,
        instance_url=None,
        advisor_object="Account",
        advisor_number_field="Advisor_Number__c",
        practice_lookup_field=None,
        task_link_field="WhatId",
        opportunity_link_field="AccountId",
        advisor_numbers=("17018", "34318", "34605", "21114", "20728"),
        task_subjects=("Call", "Virtual Meeting"),
        activity_start_date=None,
        expected_advisor_count=5,
        expected_task_count=83,
        expected_opportunity_count=4,
        advisor_extra_fields=(),
        task_extra_fields=(),
        opportunity_extra_fields=(),
        advisor_field_map={},
        task_field_map={},
    )
    base.update(overrides)
    # Mirrors the real default-mirroring logic in config.py:
    # _build_salesforce_config() -- advisor_lookup_field/advisor_lookup_values
    # default to advisor_number_field/advisor_numbers unless a test overrides
    # them explicitly.
    base.setdefault("advisor_lookup_field", base["advisor_number_field"])
    base.setdefault("advisor_lookup_values", base["advisor_numbers"])
    return SalesforceConfig(**base)


def _described(field_names: list[str]) -> dict:
    return {"queryable": True, "fields": [{"name": name, "label": name} for name in field_names]}


ACCOUNT_DESCRIBED = _described(
    ["Id", "Name", "Advisor_Number__c", "Practice__c", "OwnerId", "CreatedDate", "LastModifiedDate", "District_VP_Wholesaling__c"]
)
TASK_DESCRIBED = _described(
    [
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
        "Type",
    ]
)
OPPORTUNITY_DESCRIBED = _described(
    [
        "Id",
        "Name",
        "AccountId",
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
)


# --- Advisor query -----------------------------------------------------------


def test_advisor_query_uses_configured_object_and_field():
    soql, _omitted = sf_queries.build_advisor_query(ACCOUNT_DESCRIBED, _sf_config())
    assert soql.startswith("SELECT")
    assert " FROM Account WHERE Advisor_Number__c IN " in soql


def test_advisor_query_escapes_values_safely():
    config = _sf_config(advisor_numbers=("170'18",))
    soql, _omitted = sf_queries.build_advisor_query(ACCOUNT_DESCRIBED, config)
    # A raw, unescaped quote would let the value break out of its string
    # literal; format_soql must backslash-escape it instead.
    assert "170\\'18" in soql


def test_advisor_query_missing_number_field_raises():
    config = _sf_config(advisor_number_field="Nonexistent__c")
    with pytest.raises(SalesforceMetadataError):
        sf_queries.build_advisor_query(ACCOUNT_DESCRIBED, config)


def test_advisor_query_empty_numbers_list_raises():
    config = _sf_config(advisor_numbers=())
    with pytest.raises(MalformedSalesforceQueryError):
        sf_queries.build_advisor_query(ACCOUNT_DESCRIBED, config)


def test_advisor_query_by_name_when_lookup_field_overridden():
    config = _sf_config(
        advisor_lookup_field="Name",
        advisor_lookup_values=("Scott Syrja", "Mathis Turcotte"),
    )
    soql, _omitted = sf_queries.build_advisor_query(ACCOUNT_DESCRIBED, config)
    assert " FROM Account WHERE Name IN " in soql
    assert "Scott Syrja" in soql


def test_advisor_query_name_lookup_does_not_require_number_field():
    # advisor_number_field is still requested (best-effort, to populate the
    # advisor_number output column) but must not block extraction when it
    # doesn't exist on the object -- only advisor_lookup_field is required.
    config = _sf_config(
        advisor_number_field="Nonexistent__c",
        advisor_lookup_field="Name",
        advisor_lookup_values=("Scott Syrja",),
    )
    soql, omitted = sf_queries.build_advisor_query(ACCOUNT_DESCRIBED, config)
    assert " FROM Account WHERE Name IN " in soql
    assert "Nonexistent__c" in omitted


def test_fetch_advisors_uses_query_all():
    client = MagicMock()
    client.query_all.return_value = {"records": [{"Id": "001", "Name": "Test Advisor"}]}
    records = sf_queries.fetch_advisors(client, ACCOUNT_DESCRIBED, _sf_config())
    assert records == [{"Id": "001", "Name": "Test Advisor"}]
    client.query_all.assert_called_once()


# --- Scope resolution ---------------------------------------------------------


def test_practice_lookup_used_when_configured():
    config = _sf_config(practice_lookup_field="Practice__c")
    advisors = [{"Id": "001", "Advisor_Number__c": "17018", "Practice__c": "PRAC1"}]
    scope = sf_queries.resolve_scope(advisors, config)
    assert scope.number_to_scope_id["17018"] == "PRAC1"


def test_advisor_id_used_when_practice_lookup_blank():
    config = _sf_config(practice_lookup_field=None)
    advisors = [{"Id": "001", "Advisor_Number__c": "17018"}]
    scope = sf_queries.resolve_scope(advisors, config)
    assert scope.number_to_scope_id["17018"] == "001"


def test_missing_advisor_numbers_are_reported():
    config = _sf_config(advisor_numbers=("17018", "99999"))
    advisors = [{"Id": "001", "Advisor_Number__c": "17018"}]
    scope = sf_queries.resolve_scope(advisors, config)
    assert scope.missing_numbers == ["99999"]


def test_duplicate_advisor_number_is_reported():
    config = _sf_config(advisor_numbers=("17018",))
    advisors = [
        {"Id": "001", "Advisor_Number__c": "17018"},
        {"Id": "002", "Advisor_Number__c": "17018"},
    ]
    scope = sf_queries.resolve_scope(advisors, config)
    assert scope.duplicate_numbers == ["17018"]


def test_resolve_scope_by_name_when_lookup_field_overridden():
    config = _sf_config(
        advisor_lookup_field="Name",
        advisor_lookup_values=("Scott Syrja",),
    )
    advisors = [{"Id": "001", "Name": "Scott Syrja", "Advisor_Number__c": None}]
    scope = sf_queries.resolve_scope(advisors, config)
    assert scope.number_to_advisor["Scott Syrja"]["Id"] == "001"
    assert scope.missing_numbers == []


def test_resolve_scope_by_name_is_case_insensitive():
    config = _sf_config(
        advisor_lookup_field="Name",
        advisor_lookup_values=("scott syrja",),
    )
    advisors = [{"Id": "001", "Name": "Scott Syrja"}]
    scope = sf_queries.resolve_scope(advisors, config)
    assert scope.missing_numbers == []


# --- Task query ----------------------------------------------------------------


def test_task_query_applies_exact_subject_filter():
    soql, _omitted = sf_queries.build_task_query(TASK_DESCRIBED, ["001"], _sf_config())
    assert "Subject IN ('Call','Virtual Meeting')" in soql


def test_task_query_always_excludes_deleted():
    soql, _omitted = sf_queries.build_task_query(TASK_DESCRIBED, ["001"], _sf_config())
    assert "IsDeleted = FALSE" in soql


def test_task_query_omits_date_filter_when_blank():
    # ActivityDate is still a selected column (it's in the preferred field
    # list); what must be absent is the WHERE-clause filter condition.
    soql, _omitted = sf_queries.build_task_query(TASK_DESCRIBED, ["001"], _sf_config(activity_start_date=None))
    assert "ActivityDate >=" not in soql


def test_task_query_includes_date_filter_when_set():
    soql, _omitted = sf_queries.build_task_query(TASK_DESCRIBED, ["001"], _sf_config(activity_start_date="2025-01-01"))
    assert "ActivityDate >= 2025-01-01" in soql


def test_task_query_uses_configured_link_field():
    config = _sf_config(task_link_field="WhoId")
    soql, _omitted = sf_queries.build_task_query(TASK_DESCRIBED, ["001"], config)
    assert "WhoId IN ('001')" in soql


def test_task_query_missing_link_field_raises():
    config = _sf_config(task_link_field="Nonexistent__c")
    with pytest.raises(SalesforceMetadataError):
        sf_queries.build_task_query(TASK_DESCRIBED, ["001"], config)


def test_task_query_empty_scope_raises():
    with pytest.raises(MalformedSalesforceQueryError):
        sf_queries.build_task_query(TASK_DESCRIBED, [], _sf_config())


def test_fetch_tasks_uses_query_all_with_include_deleted():
    client = MagicMock()
    client.query_all.return_value = {"records": []}
    sf_queries.fetch_tasks(client, TASK_DESCRIBED, ["001"], _sf_config())
    _args, kwargs = client.query_all.call_args
    assert kwargs.get("include_deleted") is True


def test_optional_unavailable_field_is_omitted_not_fatal():
    config = _sf_config(task_extra_fields=("Nonexistent_Field__c",))
    soql, omitted = sf_queries.build_task_query(TASK_DESCRIBED, ["001"], config)
    assert "Nonexistent_Field__c" not in soql
    assert "Nonexistent_Field__c" in omitted


# --- Opportunity query ----------------------------------------------------------


def test_opportunity_query_uses_configured_relationship_field():
    config = _sf_config(opportunity_link_field="AccountId")
    soql, _omitted = sf_queries.build_opportunity_query(OPPORTUNITY_DESCRIBED, ["001"], config)
    assert "AccountId IN ('001')" in soql
    assert "ORDER BY IsClosed ASC, CloseDate ASC" in soql


def test_opportunity_query_missing_link_field_raises():
    config = _sf_config(opportunity_link_field="Nonexistent__c")
    with pytest.raises(SalesforceMetadataError):
        sf_queries.build_opportunity_query(OPPORTUNITY_DESCRIBED, ["001"], config)


def test_opportunity_query_empty_scope_raises():
    with pytest.raises(MalformedSalesforceQueryError):
        sf_queries.build_opportunity_query(OPPORTUNITY_DESCRIBED, [], _sf_config())


def test_fetch_opportunities_uses_query_all():
    client = MagicMock()
    client.query_all.return_value = {"records": [{"Id": "006", "Name": "Deal"}]}
    records = sf_queries.fetch_opportunities(client, OPPORTUNITY_DESCRIBED, ["001"], _sf_config())
    assert records == [{"Id": "006", "Name": "Deal"}]


# --- Task subject audit (debug/discovery only) -----------------------------------


def test_audit_task_subjects_returns_empty_for_no_scope():
    client = MagicMock()
    result = sf_queries.audit_task_subjects(client, [], _sf_config())
    assert result == []
    client.query.assert_not_called()
