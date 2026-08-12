from __future__ import annotations

import pytest

from dvp_meeting_prep.config import get_settings


def test_missing_google_cloud_project_raises(base_env):
    base_env.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    with pytest.raises(RuntimeError, match="GOOGLE_CLOUD_PROJECT"):
        get_settings()


def test_missing_google_cloud_location_raises(base_env):
    base_env.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    with pytest.raises(RuntimeError, match="GOOGLE_CLOUD_LOCATION"):
        get_settings()


def test_missing_google_genai_model_raises(base_env):
    base_env.delenv("GOOGLE_GENAI_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="GOOGLE_GENAI_MODEL"):
        get_settings()


def test_unsupported_llm_provider_raises(base_env):
    base_env.setenv("LLM_PROVIDER", "openai")
    with pytest.raises(RuntimeError, match="Unsupported LLM_PROVIDER"):
        get_settings()


def test_default_provider_is_gemini_enterprise(base_env):
    settings = get_settings()
    assert settings.gemini.provider == "gemini_enterprise"


def test_invalid_temperature_too_high_raises(base_env):
    base_env.setenv("GOOGLE_GENAI_TEMPERATURE", "3.5")
    with pytest.raises(RuntimeError, match="GOOGLE_GENAI_TEMPERATURE"):
        get_settings()


def test_invalid_temperature_negative_raises(base_env):
    base_env.setenv("GOOGLE_GENAI_TEMPERATURE", "-0.1")
    with pytest.raises(RuntimeError, match="GOOGLE_GENAI_TEMPERATURE"):
        get_settings()


def test_invalid_temperature_not_a_number_raises(base_env):
    base_env.setenv("GOOGLE_GENAI_TEMPERATURE", "hot")
    with pytest.raises(RuntimeError):
        get_settings()


def test_default_temperature_is_point_two(base_env):
    assert get_settings().gemini.temperature == 0.2


def test_invalid_max_output_tokens_zero_raises(base_env):
    base_env.setenv("GOOGLE_GENAI_MAX_OUTPUT_TOKENS", "0")
    with pytest.raises(RuntimeError, match="GOOGLE_GENAI_MAX_OUTPUT_TOKENS"):
        get_settings()


def test_invalid_max_output_tokens_negative_raises(base_env):
    base_env.setenv("GOOGLE_GENAI_MAX_OUTPUT_TOKENS", "-5")
    with pytest.raises(RuntimeError, match="GOOGLE_GENAI_MAX_OUTPUT_TOKENS"):
        get_settings()


def test_default_max_output_tokens_is_8192(base_env):
    assert get_settings().gemini.max_output_tokens == 8192


def test_invalid_timeout_raises(base_env):
    base_env.setenv("GOOGLE_GENAI_REQUEST_TIMEOUT_SECONDS", "0")
    with pytest.raises(RuntimeError, match="GOOGLE_GENAI_REQUEST_TIMEOUT_SECONDS"):
        get_settings()


def test_invalid_retry_count_raises(base_env):
    base_env.setenv("GOOGLE_GENAI_MAX_RETRIES", "-1")
    with pytest.raises(RuntimeError, match="GOOGLE_GENAI_MAX_RETRIES"):
        get_settings()


def test_default_max_retries_is_three(base_env):
    assert get_settings().gemini.max_retries == 3


def test_invalid_api_version_raises(base_env):
    base_env.setenv("GOOGLE_GENAI_API_VERSION", "")
    base_env.setenv("GOOGLE_GENAI_API_VERSION", "2")
    with pytest.raises(RuntimeError, match="GOOGLE_GENAI_API_VERSION"):
        get_settings()


def test_default_api_version_is_v1(base_env):
    assert get_settings().gemini.api_version == "v1"


def test_store_audit_content_defaults_true(base_env):
    assert get_settings().gemini.store_audit_content is True


def test_store_audit_content_can_be_disabled(base_env):
    base_env.setenv("STORE_LLM_AUDIT_CONTENT", "false")
    assert get_settings().gemini.store_audit_content is False


def test_project_location_model_are_configurable(base_env):
    base_env.setenv("GOOGLE_CLOUD_PROJECT", "my-custom-project")
    base_env.setenv("GOOGLE_CLOUD_LOCATION", "europe-west4")
    base_env.setenv("GOOGLE_GENAI_MODEL", "gemini-custom-model")
    settings = get_settings()
    assert settings.gemini.project == "my-custom-project"
    assert settings.gemini.location == "europe-west4"
    assert settings.gemini.model == "gemini-custom-model"


def test_no_api_key_field_exists_on_gemini_config(base_env):
    # Authentication is ADC-only -- there must be no api_key/access_token-shaped
    # field to accidentally populate from an OPENAI_API_KEY/GEMINI_API_KEY-style
    # var. (max_output_tokens is a legitimate token *count*, not a credential.)
    settings = get_settings()
    field_names = set(settings.gemini.__dataclass_fields__.keys())
    suspicious = {"api_key", "apikey", "access_token", "secret", "credential", "credentials"}
    assert not any(bad in name.lower() for name in field_names for bad in suspicious)
