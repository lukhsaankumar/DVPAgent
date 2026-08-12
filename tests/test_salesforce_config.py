from __future__ import annotations

import pytest

from dvp_meeting_prep import config as config_module
from dvp_meeting_prep.config import get_settings


def test_defaults_select_salesforce_and_sandbox(base_env):
    settings = get_settings()
    assert settings.data_source == "salesforce"
    assert settings.app_env == "sandbox"
    assert settings.salesforce.auth_mode == "password"


def test_advisor_numbers_default_to_the_five_business_numbers(base_env):
    settings = get_settings()
    assert settings.salesforce.advisor_numbers == ("17018", "34318", "34605", "21114", "20728")


def test_advisor_numbers_parsing_trims_whitespace(base_env):
    base_env.setenv("SF_ADVISOR_NUMBERS", " 17018 , 34318,34605 ")
    settings = get_settings()
    assert settings.salesforce.advisor_numbers == ("17018", "34318", "34605")


def test_task_subjects_default_preserves_call_and_virtual_meeting(base_env):
    settings = get_settings()
    assert settings.salesforce.task_subjects == ("Call", "Virtual Meeting")


def test_unsupported_data_source_raises(base_env):
    base_env.setenv("DATA_SOURCE", "sharepoint")
    with pytest.raises(RuntimeError, match="Unsupported DATA_SOURCE"):
        get_settings()


def test_unsupported_app_env_raises(base_env):
    base_env.setenv("APP_ENV", "staging")
    with pytest.raises(RuntimeError, match="Unsupported APP_ENV"):
        get_settings()


def test_unsupported_auth_mode_raises(base_env):
    base_env.setenv("SF_AUTH_MODE", "oauth")
    with pytest.raises(RuntimeError, match="Unsupported SF_AUTH_MODE"):
        get_settings()


def test_missing_required_env_var_raises(clean_env):
    clean_env.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    clean_env.setenv("GOOGLE_GENAI_MODEL", "gemini-test-model")
    with pytest.raises(RuntimeError, match="GOOGLE_CLOUD_PROJECT"):
        get_settings()


def test_data_source_does_not_require_salesforce_credentials(base_env):
    # Settings loading must not require SF creds to exist -- DATA_SOURCE could
    # be csv, or salesforce credentials might only be validated at connect time.
    settings = get_settings()
    assert settings.salesforce.username is None
    assert settings.salesforce.password is None


def test_invalid_activity_start_date_raises(base_env):
    base_env.setenv("SF_ACTIVITY_START_DATE", "01/15/2025")
    with pytest.raises(RuntimeError, match="SF_ACTIVITY_START_DATE"):
        get_settings()


def test_valid_activity_start_date_is_kept(base_env):
    base_env.setenv("SF_ACTIVITY_START_DATE", "2025-01-15")
    settings = get_settings()
    assert settings.salesforce.activity_start_date == "2025-01-15"


def test_field_map_overrides_merge_over_defaults(base_env):
    base_env.setenv("SF_ADVISOR_FIELD_MAP", "pwm=Is_PWM__c,book_size=Total_Book_Size__c")
    settings = get_settings()
    field_map = settings.salesforce.advisor_field_map
    assert field_map["pwm"] == "Is_PWM__c"
    assert field_map["book_size"] == "Total_Book_Size__c"
    # Untouched keys keep their default guess.
    assert field_map["area"] == "Area__c"


def test_field_map_rejects_malformed_entry(base_env):
    base_env.setenv("SF_ADVISOR_FIELD_MAP", "not-a-pair")
    with pytest.raises(RuntimeError, match="Invalid field map entry"):
        get_settings()


def test_strict_expected_counts_bool_parsing(base_env):
    base_env.setenv("SF_STRICT_EXPECTED_COUNTS", "true")
    assert get_settings().salesforce.strict_expected_counts is True


def test_bool_parsing_rejects_garbage(base_env):
    base_env.setenv("SF_STRICT_EXPECTED_COUNTS", "maybe")
    with pytest.raises(RuntimeError, match="boolean"):
        get_settings()


def test_expected_counts_default_to_business_context_values(base_env):
    settings = get_settings()
    assert settings.salesforce.expected_advisor_count == 5
    assert settings.salesforce.expected_task_count == 83
    assert settings.salesforce.expected_opportunity_count == 4


def test_env_file_resolution_prefers_explicit_env_file(clean_env, tmp_path):
    (tmp_path / ".env.custom").write_text("GOOGLE_CLOUD_PROJECT=from-custom-file\n", encoding="utf-8")
    clean_env.setenv("ENV_FILE", ".env.custom")
    clean_env.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    clean_env.setenv("GOOGLE_GENAI_MODEL", "gemini-test-model")
    settings = get_settings()
    assert settings.env_file_used == ".env.custom"
    assert settings.gemini.project == "from-custom-file"


def test_env_file_resolution_derives_from_app_env(clean_env, tmp_path):
    (tmp_path / ".env.sandbox").write_text("GOOGLE_CLOUD_PROJECT=from-sandbox-file\n", encoding="utf-8")
    clean_env.setenv("APP_ENV", "sandbox")
    clean_env.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    clean_env.setenv("GOOGLE_GENAI_MODEL", "gemini-test-model")
    settings = get_settings()
    assert settings.env_file_used == ".env.sandbox"
    assert settings.gemini.project == "from-sandbox-file"


def test_os_environment_variable_wins_over_dotenv_file(clean_env, tmp_path):
    (tmp_path / ".env.sandbox").write_text("GOOGLE_CLOUD_PROJECT=from-file\n", encoding="utf-8")
    clean_env.setenv("APP_ENV", "sandbox")
    clean_env.setenv("GOOGLE_CLOUD_PROJECT", "from-os-env")
    clean_env.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    clean_env.setenv("GOOGLE_GENAI_MODEL", "gemini-test-model")
    settings = get_settings()
    assert settings.gemini.project == "from-os-env"


def test_configure_logging_rejects_unknown_level(monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    with pytest.raises(RuntimeError, match="LOG_LEVEL"):
        config_module.configure_logging("NOT_A_LEVEL")
