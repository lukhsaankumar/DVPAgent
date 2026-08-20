from __future__ import annotations

import pytest

from dvp_meeting_prep.config import SalesforceConfig
from dvp_meeting_prep.salesforce.errors import SalesforceValidationError
from dvp_meeting_prep.salesforce.normalize import NormalizationResult
from dvp_meeting_prep.salesforce.queries import resolve_scope
from dvp_meeting_prep.salesforce.validate import run_validation


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
        advisor_numbers=("17018", "34318", "34605", "21114", "20728"),
        task_subjects=("Call", "Virtual Meeting"),
        activity_start_date=None,
        expected_advisor_count=5,
        expected_task_count=2,
        expected_opportunity_count=1,
        advisor_extra_fields=(),
        task_extra_fields=(),
        opportunity_extra_fields=(),
        advisor_field_map={},
        task_field_map={},
        strict_expected_counts=False,
    )
    base.update(overrides)
    base.setdefault("advisor_lookup_field", base["advisor_number_field"])
    base.setdefault("advisor_lookup_values", base["advisor_numbers"])
    return SalesforceConfig(**base)


def _scope(found_numbers: list[str], sf_config: SalesforceConfig):
    """Build a real ScopeResolution via resolve_scope() (not a hand-built
    stub) so missing/duplicate-number detection is exercised for real.
    """
    advisor_records = [{"Id": f"00{i}", "Advisor_Number__c": number} for i, number in enumerate(found_numbers)]
    return resolve_scope(advisor_records, sf_config)


def _normalization(rows: list[dict]) -> NormalizationResult:
    result = NormalizationResult()
    result.legacy_rows = rows
    return result


def test_matching_counts_pass():
    config = _sf_config()
    scope = _scope(["17018", "34318", "34605", "21114", "20728"], config)
    tasks = [{"Id": "t1", "WhatId": "000", "Subject": "Call"}, {"Id": "t2", "WhatId": "000", "Subject": "Virtual Meeting"}]
    opportunities = [{"Id": "o1", "AccountId": "000"}]
    report = run_validation(
        scope=scope,
        task_records=tasks,
        opportunity_records=opportunities,
        normalization=_normalization([]),
        sf_config=config,
    )
    assert report.passed
    assert all(c.passed for c in report.count_checks)


def test_count_mismatch_warns_in_nonstrict_mode():
    config = _sf_config(strict_expected_counts=False)
    scope = _scope(["17018"], config)
    report = run_validation(
        scope=scope,
        task_records=[],
        opportunity_records=[],
        normalization=_normalization([]),
        sf_config=config,
    )
    assert not report.passed
    assert any("Advisors" in p for p in report.problems)


def test_count_mismatch_raises_in_strict_mode():
    config = _sf_config(strict_expected_counts=True)
    scope = _scope(["17018"], config)
    with pytest.raises(SalesforceValidationError):
        run_validation(
            scope=scope,
            task_records=[],
            opportunity_records=[],
            normalization=_normalization([]),
            sf_config=config,
        )


def test_missing_advisor_number_reported():
    config = _sf_config(advisor_numbers=("17018", "99999"), expected_advisor_count=1)
    scope = _scope(["17018"], config)
    report = run_validation(
        scope=scope,
        task_records=[],
        opportunity_records=[],
        normalization=_normalization([]),
        sf_config=config,
    )
    assert any("99999" in p for p in report.problems)


def test_duplicate_task_ids_reported():
    config = _sf_config(advisor_numbers=("17018",), expected_advisor_count=1, expected_task_count=2)
    scope = _scope(["17018"], config)
    tasks = [{"Id": "t1", "WhatId": "000", "Subject": "Call"}, {"Id": "t1", "WhatId": "000", "Subject": "Call"}]
    report = run_validation(
        scope=scope,
        task_records=tasks,
        opportunity_records=[],
        normalization=_normalization([]),
        sf_config=config,
    )
    assert any("Duplicate Task IDs" in p for p in report.problems)


def test_unexpected_subject_reported():
    config = _sf_config(advisor_numbers=("17018",), expected_advisor_count=1, expected_task_count=1)
    scope = _scope(["17018"], config)
    tasks = [{"Id": "t1", "WhatId": "000", "Subject": "Email"}]
    report = run_validation(
        scope=scope,
        task_records=tasks,
        opportunity_records=[],
        normalization=_normalization([]),
        sf_config=config,
    )
    assert any("unexpected Subject" in p for p in report.problems)


def test_schema_mismatch_detected():
    config = _sf_config(advisor_numbers=("17018",), expected_advisor_count=1, expected_task_count=0, expected_opportunity_count=0)
    scope = _scope(["17018"], config)
    bad_rows = [{"advisor_name": "X"}]  # missing every other legacy column
    report = run_validation(
        scope=scope,
        task_records=[],
        opportunity_records=[],
        normalization=_normalization(bad_rows),
        sf_config=config,
    )
    assert any("schema" in p.lower() for p in report.problems)
