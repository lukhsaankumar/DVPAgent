from __future__ import annotations

import pytest

from dvp_meeting_prep import config as config_module

# Every env var get_settings()/_build_salesforce_config() might read. Cleared
# at the start of `clean_env` so a developer's real shell exports or repo
# .env files can never leak into a test's expectations.
_ENV_VARS_TO_CLEAR = [
    "SUPABASE_URL",
    "SUPABASE_PUBLISHABLE_KEY",
    "SUPABASE_SECRET_KEY",
    "SUPABASE_URI",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "DATA_SOURCE",
    "APP_ENV",
    "ENV_FILE",
    "LOG_LEVEL",
    "SF_DEBUG",
    "SF_DEBUG_SAMPLE_SIZE",
    "SF_SAVE_DEBUG_EXTRACTS",
    "SF_STRICT_EXPECTED_COUNTS",
    "SF_AUTH_MODE",
    "SF_USERNAME",
    "SF_PASSWORD",
    "SF_SECURITY_TOKEN",
    "SF_ACCESS_TOKEN",
    "SF_INSTANCE_URL",
    "SF_ADVISOR_OBJECT",
    "SF_ADVISOR_NUMBER_FIELD",
    "SF_PRACTICE_LOOKUP_FIELD",
    "SF_TASK_LINK_FIELD",
    "SF_OPPORTUNITY_LINK_FIELD",
    "SF_ADVISOR_NUMBERS",
    "SF_TASK_SUBJECTS",
    "SF_ACTIVITY_START_DATE",
    "SF_EXPECTED_ADVISOR_COUNT",
    "SF_EXPECTED_TASK_COUNT",
    "SF_EXPECTED_OPPORTUNITY_COUNT",
    "SF_ADVISOR_EXTRA_FIELDS",
    "SF_TASK_EXTRA_FIELDS",
    "SF_OPPORTUNITY_EXTRA_FIELDS",
    "SF_ADVISOR_FIELD_MAP",
    "SF_TASK_FIELD_MAP",
    "CSV_INPUT_PATH",
]


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """Isolate config-loading tests from the developer's real .env files and
    shell-exported env vars, and reset get_settings()'s lru_cache so each
    test starts from a blank slate.
    """
    for name in _ENV_VARS_TO_CLEAR:
        monkeypatch.delenv(name, raising=False)
    # Point PROJECT_ROOT at an empty temp dir so load_dotenv(".env"/".env.sandbox"/...)
    # finds nothing, regardless of what actually exists in the real repo root.
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    config_module.get_settings.cache_clear()
    yield monkeypatch
    config_module.get_settings.cache_clear()


@pytest.fixture
def base_env(clean_env):
    """clean_env plus the minimum required vars so get_settings() succeeds."""
    clean_env.setenv("SUPABASE_URL", "https://example.supabase.co")
    clean_env.setenv("SUPABASE_SECRET_KEY", "test-secret-key")
    clean_env.setenv("OPENAI_API_KEY", "test-openai-key")
    return clean_env
