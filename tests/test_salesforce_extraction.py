from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from simple_salesforce.exceptions import SalesforceMalformedRequest

from dvp_meeting_prep.config import GeminiConfig, SalesforceConfig, Settings, SQLiteConfig
from dvp_meeting_prep.salesforce import queries as sf_queries
from dvp_meeting_prep.salesforce.extraction import run_extraction


def _settings(*, debug: bool) -> Settings:
    sqlite_config = SQLiteConfig(
        db_path=Path("data/dvp_meeting_prep.sqlite3"),
        busy_timeout_ms=10000,
        journal_mode="WAL",
        foreign_keys=True,
        synchronous="NORMAL",
        debug=False,
    )
    gemini_config = GeminiConfig(
        provider="gemini_enterprise",
        project="test-project",
        location="us-central1",
        model="gemini-test-model",
        api_version="v1",
        temperature=0.2,
        max_output_tokens=8192,
        request_timeout_seconds=120,
        max_retries=3,
        store_audit_content=True,
    )
    sf_config = SalesforceConfig(
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
        advisor_numbers=("17018",),
        advisor_lookup_field="Name",
        advisor_lookup_values=("Scott Syrja",),
        task_subjects=("Call", "Virtual Meeting"),
        activity_start_date=None,
        expected_advisor_count=1,
        expected_task_count=0,
        expected_opportunity_count=0,
        advisor_extra_fields=(),
        task_extra_fields=(),
        opportunity_extra_fields=(),
        debug=debug,
    )
    return Settings(
        database_backend="sqlite",
        sqlite=sqlite_config,
        data_source="salesforce",
        advisor_source_mode="auto",
        app_env="sandbox",
        env_file_used=".env",
        csv_input_path=None,
        salesforce=sf_config,
        gemini=gemini_config,
    )


def _run_with_mocks(settings: Settings, *, audit_side_effect=None):
    """Run run_extraction() with connect/describe/fetch_advisors/resolve_scope/
    fetch_tasks/fetch_opportunities mocked out, but the real normalize/validate
    code paths still exercised against those fake records -- so this checks
    real end-to-end behavior of the try/except around audit_task_subjects,
    not just that it was called."""
    scope = sf_queries.ScopeResolution(
        number_to_advisor={"Scott Syrja": {"Id": "001", "Name": "Scott Syrja"}},
        number_to_scope_id={"Scott Syrja": "001"},
        missing_numbers=[],
        duplicate_numbers=[],
        scope_ids=["001"],
    )
    mock_sf_client = MagicMock(connect=MagicMock(return_value=MagicMock()))
    mock_metadata = MagicMock(describe_object=MagicMock(return_value={}))
    mock_queries = MagicMock(
        fetch_advisors=MagicMock(return_value=[{"Id": "001", "Name": "Scott Syrja"}]),
        resolve_scope=MagicMock(return_value=scope),
        audit_task_subjects=MagicMock(side_effect=audit_side_effect),
        fetch_tasks=MagicMock(return_value=[]),
        fetch_opportunities=MagicMock(return_value=[]),
    )
    with (
        patch("dvp_meeting_prep.salesforce.extraction.sf_client", mock_sf_client),
        patch("dvp_meeting_prep.salesforce.extraction.metadata", mock_metadata),
        patch("dvp_meeting_prep.salesforce.extraction.sf_queries", mock_queries),
    ):
        result = run_extraction(settings)
    return result, mock_queries


def test_audit_task_subjects_failure_does_not_abort_extraction():
    # Reproduces the real-world failure: an org where Task.Subject can't be
    # used in a SOQL GROUP BY raises SalesforceMalformedRequest from the
    # debug-only subject-count audit. That must not prevent the actual
    # Task/Opportunity fetch and normalization from completing.
    settings = _settings(debug=True)
    error = SalesforceMalformedRequest(
        "url", 400, "Task", [{"message": "field 'Subject' can not be grouped in a query call"}]
    )
    result, mock_queries = _run_with_mocks(settings, audit_side_effect=error)

    mock_queries.audit_task_subjects.assert_called_once()
    mock_queries.fetch_tasks.assert_called_once()
    mock_queries.fetch_opportunities.assert_called_once()
    assert result.legacy_rows == []
    assert result.validation.passed


def test_audit_task_subjects_skipped_when_debug_disabled():
    settings = _settings(debug=False)
    _result, mock_queries = _run_with_mocks(settings)
    mock_queries.audit_task_subjects.assert_not_called()
