from __future__ import annotations

import pytest

from dvp_meeting_prep import advisors as advisors_module
from dvp_meeting_prep import config as config_module
from dvp_meeting_prep.config import SQLiteConfig
from dvp_meeting_prep.db import Database

# Every env var get_settings()/_build_salesforce_config() might read. Cleared
# at the start of `clean_env` so a developer's real shell exports or repo
# .env files can never leak into a test's expectations.
_ENV_VARS_TO_CLEAR = [
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
    "LLM_PROVIDER",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_GENAI_MODEL",
    "GOOGLE_GENAI_API_VERSION",
    "GOOGLE_GENAI_TEMPERATURE",
    "GOOGLE_GENAI_MAX_OUTPUT_TOKENS",
    "GOOGLE_GENAI_REQUEST_TIMEOUT_SECONDS",
    "GOOGLE_GENAI_MAX_RETRIES",
    "STORE_LLM_AUDIT_CONTENT",
    "DATABASE_BACKEND",
    "SQLITE_DB_PATH",
    "SQLITE_BUSY_TIMEOUT_MS",
    "SQLITE_JOURNAL_MODE",
    "SQLITE_FOREIGN_KEYS",
    "SQLITE_SYNCHRONOUS",
    "SQLITE_DEBUG",
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
    clean_env.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    clean_env.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    clean_env.setenv("GOOGLE_GENAI_MODEL", "gemini-test-model")
    return clean_env


@pytest.fixture
def sqlite_db(tmp_path):
    """A real SQLite database file under tmp_path, schema already applied.

    Never touches a developer's real database -- db_path always lives under
    pytest's tmp_path, regardless of SQLITE_DB_PATH/DATABASE_BACKEND in the
    environment or any real .env file.
    """
    config = SQLiteConfig(
        db_path=tmp_path / "test.sqlite3",
        busy_timeout_ms=2000,
        journal_mode="WAL",
        foreign_keys=True,
        synchronous="NORMAL",
        debug=False,
    )
    database = Database(config)
    database.ensure_schema_ready()
    return database


@pytest.fixture(autouse=True)
def _reset_advisor_name_cache():
    """advisors.py caches the distinct advisor name list at module scope
    (keyed per salesforce_table) -- reset it around every test so one test's
    data can never leak into another's list_advisor_names()/
    search_advisor_names() results.
    """
    advisors_module._cache.clear()
    yield
    advisors_module._cache.clear()
