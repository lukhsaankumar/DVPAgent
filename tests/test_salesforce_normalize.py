from __future__ import annotations

from dvp_meeting_prep.config import SalesforceConfig
from dvp_meeting_prep.salesforce import normalize as sf_normalize
from dvp_meeting_prep.salesforce.normalize import LEGACY_ROW_COLUMNS, build_legacy_rows, strip_attributes
from dvp_meeting_prep.salesforce.queries import ScopeResolution


def _sf_config(**overrides) -> SalesforceConfig:
    base = dict(
        auth_mode="password",
        username=None,
        password=None,
        security_token=None,
        access_token=None,
        instance_url=None,
        advisor_object="Account",
        advisor_number_field="Advisor_Number__c",
        practice_lookup_field=None,
        task_link_field="WhatId",
        opportunity_link_field="AccountId",
        advisor_numbers=("17018",),
        task_subjects=("Call", "Virtual Meeting"),
        activity_start_date=None,
        expected_advisor_count=1,
        expected_task_count=1,
        expected_opportunity_count=0,
        advisor_extra_fields=(),
        task_extra_fields=(),
        opportunity_extra_fields=(),
        advisor_field_map={
            "district_vp_wholesaling": "District_VP_Wholesaling__c",
            "pwm": "PWM__c",
            "book_size": "Book_Size__c",
            "assets_under_management": "Assets_Under_Management__c",
            "new_business_ytd": "New_Business_YTD__c",
            "area": "Area__c",
            "region_office_number": "Region_Office_Number__c",
            "start_date": "Start_Date__c",
            "assigned": "Owner.Name",
        },
        task_field_map={
            "task_subtype": "TaskSubtype",
            "subject": "Subject",
            "comments": "Description",
            "interaction_type": "Type",
            "completed_date_time": "CompletedDateTime",
            "created_date": "CreatedDate",
            "status": "Status",
        },
    )
    base.update(overrides)
    base.setdefault("advisor_lookup_field", base["advisor_number_field"])
    base.setdefault("advisor_lookup_values", base["advisor_numbers"])
    return SalesforceConfig(**base)


def test_strip_attributes_removes_rest_metadata():
    record = {"attributes": {"type": "Account", "url": "/services/..."}, "Id": "001", "Name": "Acme"}
    cleaned = strip_attributes(record)
    assert "attributes" not in cleaned
    assert cleaned == {"Id": "001", "Name": "Acme"}


def test_strip_attributes_flattens_nested_relationships():
    record = {
        "attributes": {"type": "Task"},
        "Id": "00T1",
        "Owner": {"attributes": {"type": "User"}, "Name": "Leo Waverly"},
    }
    cleaned = strip_attributes(record)
    assert cleaned["Owner"] == {"Name": "Leo Waverly"}


def _scope_for_one_advisor(advisor: dict) -> ScopeResolution:
    scope = ScopeResolution()
    number = advisor["Advisor_Number__c"]
    scope.number_to_advisor[number] = advisor
    scope.number_to_scope_id[number] = advisor["Id"]
    scope.scope_ids = [advisor["Id"]]
    return scope


def test_legacy_row_has_every_contract_column():
    advisor = {
        "Id": "001",
        "Name": "Avery Benton",
        "Advisor_Number__c": "17018",
        "District_VP_Wholesaling__c": "Marcus Bellamy",
        "PWM__c": "No",
        "Owner": {"Name": "Leo Waverly"},
    }
    task = {
        "Id": "00T1",
        "WhatId": "001",
        "Subject": "Call",
        "TaskSubtype": "Call",
        "Description": "Discussed portfolio",
        "Type": "Phone",
        "CompletedDateTime": "2026-01-20T08:55:00.000+0000",
        "CreatedDate": "2026-01-20T00:00:00.000+0000",
        "Status": "Completed",
    }
    config = _sf_config()
    scope = _scope_for_one_advisor(advisor)

    result = build_legacy_rows([advisor], [task], scope, config)

    assert len(result.legacy_rows) == 1
    row = result.legacy_rows[0]
    assert set(row.keys()) == set(LEGACY_ROW_COLUMNS)
    assert row["advisor_name"] == "Avery Benton"
    assert row["advisor_number"] == "17018"
    assert row["subject"] == "Call"
    assert row["comments"] == "Discussed portfolio"
    assert row["district_vp_wholesaling"] == "Marcus Bellamy"
    assert row["assigned"] == "Leo Waverly"


def test_unmatched_task_is_reported_not_silently_dropped():
    advisor = {"Id": "001", "Name": "Avery Benton", "Advisor_Number__c": "17018"}
    task = {"Id": "00T9", "WhatId": "999-does-not-exist", "Subject": "Call"}
    config = _sf_config()
    scope = _scope_for_one_advisor(advisor)

    result = build_legacy_rows([advisor], [task], scope, config)

    assert result.legacy_rows == []
    assert len(result.dropped) == 1
    assert result.dropped[0]["id"] == "00T9"
    assert "999-does-not-exist" in result.dropped[0]["reason"]


def test_duplicate_task_ids_are_detected():
    advisor = {"Id": "001", "Name": "Avery Benton", "Advisor_Number__c": "17018"}
    task = {"Id": "00T1", "WhatId": "001", "Subject": "Call"}
    config = _sf_config()
    scope = _scope_for_one_advisor(advisor)

    result = build_legacy_rows([advisor], [task, dict(task)], scope, config)

    assert result.duplicate_task_ids == ["00T1"]
    # Both rows are still preserved (not deduped away) -- Salesforce data
    # integrity issues surface, they aren't silently corrected.
    assert len(result.legacy_rows) == 2


def test_find_duplicate_ids_ignores_records_without_id():
    records = [{"Id": "1"}, {"Id": "1"}, {"Name": "no id"}]
    assert sf_normalize.find_duplicate_ids(records) == ["1"]
