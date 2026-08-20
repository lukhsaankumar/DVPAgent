from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from dvp_meeting_prep import data_source
from dvp_meeting_prep.config import GeminiConfig, SalesforceConfig, Settings, SQLiteConfig


def _settings(data_source_value: str, csv_input_path: str | None = None) -> Settings:
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
        advisor_lookup_field="Advisor_Number__c",
        advisor_lookup_values=("17018",),
        task_subjects=("Call", "Virtual Meeting"),
        activity_start_date=None,
        expected_advisor_count=5,
        expected_task_count=83,
        expected_opportunity_count=4,
        advisor_extra_fields=(),
        task_extra_fields=(),
        opportunity_extra_fields=(),
    )
    return Settings(
        database_backend="sqlite",
        sqlite=sqlite_config,
        data_source=data_source_value,
        advisor_source_mode="legacy",
        app_env="sandbox",
        env_file_used=".env",
        csv_input_path=csv_input_path,
        salesforce=sf_config,
        gemini=gemini_config,
    )


def test_csv_data_source_uses_read_salesforce_rows():
    settings = _settings("csv", csv_input_path="some/path.xlsx")
    with patch("dvp_meeting_prep.data_source.read_salesforce_rows") as mock_read:
        mock_read.return_value = [{"advisor_name": "Test"}]
        rows = data_source.load_salesforce_source_data(settings)
    mock_read.assert_called_once_with("some/path.xlsx")
    assert rows == [{"advisor_name": "Test"}]


def test_csv_data_source_falls_back_to_default_sample_path_when_unset():
    settings = _settings("csv", csv_input_path=None)
    with patch("dvp_meeting_prep.data_source.read_salesforce_rows") as mock_read:
        mock_read.return_value = []
        data_source.load_salesforce_source_data(settings)
    called_path = mock_read.call_args[0][0]
    assert "Salesforce Spreadsheet Input" in called_path


def test_csv_explicit_path_argument_wins_over_settings():
    settings = _settings("csv", csv_input_path="from/settings.xlsx")
    with patch("dvp_meeting_prep.data_source.read_salesforce_rows") as mock_read:
        mock_read.return_value = []
        data_source.load_salesforce_source_data(settings, csv_path="from/call/site.xlsx")
    mock_read.assert_called_once_with("from/call/site.xlsx")


def test_salesforce_data_source_uses_the_salesforce_extraction_pipeline():
    settings = _settings("salesforce")
    fake_result = type("FakeResult", (), {"legacy_rows": [{"advisor_name": "SF Advisor"}]})()
    with patch("dvp_meeting_prep.salesforce.extraction.run_extraction") as mock_run:
        mock_run.return_value = fake_result
        rows = data_source.load_salesforce_source_data(settings)
    mock_run.assert_called_once()
    assert rows == [{"advisor_name": "SF Advisor"}]


def test_dry_run_flag_is_forwarded_to_extraction():
    settings = _settings("salesforce")
    fake_result = type("FakeResult", (), {"legacy_rows": []})()
    with patch("dvp_meeting_prep.salesforce.extraction.run_extraction") as mock_run:
        mock_run.return_value = fake_result
        data_source.load_salesforce_source_data(settings, dry_run=True)
    _args, kwargs = mock_run.call_args
    assert kwargs.get("dry_run") is True


def test_unsupported_data_source_raises():
    settings = _settings("sharepoint")
    with pytest.raises(RuntimeError, match="Unsupported DATA_SOURCE"):
        data_source.load_salesforce_source_data(settings)
