from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
import logging
import os

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]

VALID_DATA_SOURCES = {"salesforce", "csv"}
VALID_APP_ENVS = {"sandbox", "production", "custom"}
VALID_SF_AUTH_MODES = {"password", "access_token"}
VALID_LLM_PROVIDERS = {"gemini_enterprise"}
VALID_DATABASE_BACKENDS = {"sqlite"}
VALID_SQLITE_JOURNAL_MODES = {"WAL", "DELETE", "TRUNCATE", "PERSIST", "MEMORY", "OFF"}
VALID_SQLITE_SYNCHRONOUS_MODES = {"OFF", "NORMAL", "FULL", "EXTRA"}

# Best-guess custom-field API names for the legacy salesforce_data columns that
# don't have an obvious standard-field equivalent. These are almost certainly
# wrong for any specific org -- they exist so the app has *something* to query
# out of the box, and are meant to be overridden via SF_ADVISOR_FIELD_MAP /
# SF_TASK_FIELD_MAP once the real API names are known (see
# `python -m scripts.salesforce_extract --discover-salesforce`).
DEFAULT_ADVISOR_FIELD_MAP: dict[str, str] = {
    "district_vp_wholesaling": "District_VP_Wholesaling__c",
    "pwm": "PWM__c",
    "book_size": "Book_Size__c",
    "assets_under_management": "Assets_Under_Management__c",
    "new_business_ytd": "New_Business_YTD__c",
    "area": "Area__c",
    "region_office_number": "Region_Office_Number__c",
    "start_date": "Start_Date__c",
    "assigned": "Owner.Name",
}

# task_subtype, subject, comments, completed_date_time, created_date, and
# status map to real Salesforce standard Task fields, so these defaults are
# high-confidence. interaction_type has no clean standard equivalent.
DEFAULT_TASK_FIELD_MAP: dict[str, str] = {
    "task_subtype": "TaskSubtype",
    "subject": "Subject",
    "comments": "Description",
    "interaction_type": "Type",
    "completed_date_time": "CompletedDateTime",
    "created_date": "CreatedDate",
    "status": "Status",
}


def _parse_bool(value: str | None, default: bool) -> bool:
    text = (value or "").strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    raise RuntimeError(f"Could not parse boolean environment value: {value!r}. Use true/false.")


def _parse_int(value: str | None, default: int) -> int:
    text = (value or "").strip()
    if not text:
        return default
    try:
        return int(text)
    except ValueError as exc:
        raise RuntimeError(f"Could not parse integer environment value: {value!r}.") from exc


def _parse_float(value: str | None, default: float) -> float:
    text = (value or "").strip()
    if not text:
        return default
    try:
        return float(text)
    except ValueError as exc:
        raise RuntimeError(f"Could not parse numeric environment value: {value!r}.") from exc


def _parse_csv_list(value: str | None) -> tuple[str, ...]:
    text = (value or "").strip()
    if not text:
        return ()
    return tuple(item.strip() for item in text.split(",") if item.strip())


def _parse_field_map(value: str | None, defaults: dict[str, str]) -> dict[str, str]:
    """Parse `legacy_column=SalesforceField,legacy_column=SalesforceField` pairs.

    Starts from `defaults` so the app has a working (if best-guess) mapping
    out of the box; entries in `value` override individual keys without
    requiring the whole map to be restated.
    """
    merged = dict(defaults)
    text = (value or "").strip()
    if not text:
        return merged
    for pair in text.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise RuntimeError(f"Invalid field map entry {pair!r}; expected format legacy_column=SalesforceField.")
        key, _, target = pair.partition("=")
        key, target = key.strip(), target.strip()
        if not key or not target:
            raise RuntimeError(f"Invalid field map entry {pair!r}; expected format legacy_column=SalesforceField.")
        merged[key] = target
    return merged


def validate_iso_date(value: str) -> str:
    text = value.strip()
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise RuntimeError(f"SF_ACTIVITY_START_DATE must be an ISO date (YYYY-MM-DD), got: {value!r}.") from exc
    return text


@dataclass(frozen=True)
class SalesforceConfig:
    auth_mode: str
    username: str | None
    password: str | None
    security_token: str | None
    access_token: str | None
    instance_url: str | None

    advisor_object: str
    advisor_number_field: str
    practice_lookup_field: str | None
    task_link_field: str
    opportunity_link_field: str

    advisor_numbers: tuple[str, ...]
    task_subjects: tuple[str, ...]
    activity_start_date: str | None

    expected_advisor_count: int
    expected_task_count: int
    expected_opportunity_count: int

    advisor_extra_fields: tuple[str, ...]
    task_extra_fields: tuple[str, ...]
    opportunity_extra_fields: tuple[str, ...]

    advisor_field_map: dict[str, str] = field(default_factory=dict)
    task_field_map: dict[str, str] = field(default_factory=dict)

    debug: bool = False
    debug_sample_size: int = 3
    save_debug_extracts: bool = False
    strict_expected_counts: bool = False


@dataclass(frozen=True)
class GeminiConfig:
    """Gemini Enterprise (google-genai) settings. See docs/GEMINI_SQLITE_SETUP.md.

    Authentication uses Google Application Default Credentials -- there is no
    API key field here by design (see llm.py / docs for the `gcloud auth
    application-default login` setup flow this expects externally).
    """

    provider: str
    project: str
    location: str
    model: str
    api_version: str
    temperature: float
    max_output_tokens: int
    request_timeout_seconds: int
    max_retries: int
    store_audit_content: bool


def _build_gemini_config() -> GeminiConfig:
    provider = os.environ.get("LLM_PROVIDER", "gemini_enterprise").strip().lower() or "gemini_enterprise"
    if provider not in VALID_LLM_PROVIDERS:
        raise RuntimeError(f"Unsupported LLM_PROVIDER: {provider!r}. Expected one of {sorted(VALID_LLM_PROVIDERS)}.")

    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
    if not project:
        raise RuntimeError(
            "GOOGLE_CLOUD_PROJECT is missing from the environment. This is the "
            "enterprise-controlled Google Cloud project ID -- ask your platform "
            "team for the correct value; it is not something to guess."
        )

    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "").strip()
    if not location:
        raise RuntimeError(
            "GOOGLE_CLOUD_LOCATION is missing from the environment (e.g. 'us-central1'). "
            "This is enterprise-controlled -- ask your platform team for the correct value."
        )

    model = os.environ.get("GOOGLE_GENAI_MODEL", "").strip()
    if not model:
        raise RuntimeError(
            "GOOGLE_GENAI_MODEL is missing from the environment. This is the "
            "enterprise-approved Gemini model name -- ask your platform team "
            "which model you're allowed to call."
        )

    api_version = os.environ.get("GOOGLE_GENAI_API_VERSION", "v1").strip() or "v1"
    if not api_version.startswith("v"):
        raise RuntimeError(f"Invalid GOOGLE_GENAI_API_VERSION: {api_version!r}. Expected something like 'v1'.")

    temperature = _parse_float(os.environ.get("GOOGLE_GENAI_TEMPERATURE"), 0.2)
    if not (0.0 <= temperature <= 2.0):
        raise RuntimeError(f"Invalid GOOGLE_GENAI_TEMPERATURE: {temperature!r}. Expected a value between 0.0 and 2.0.")

    max_output_tokens = _parse_int(os.environ.get("GOOGLE_GENAI_MAX_OUTPUT_TOKENS"), 8192)
    if max_output_tokens <= 0:
        raise RuntimeError(f"Invalid GOOGLE_GENAI_MAX_OUTPUT_TOKENS: {max_output_tokens!r}. Expected a positive integer.")

    request_timeout_seconds = _parse_int(os.environ.get("GOOGLE_GENAI_REQUEST_TIMEOUT_SECONDS"), 120)
    if request_timeout_seconds <= 0:
        raise RuntimeError(
            f"Invalid GOOGLE_GENAI_REQUEST_TIMEOUT_SECONDS: {request_timeout_seconds!r}. Expected a positive integer."
        )

    max_retries = _parse_int(os.environ.get("GOOGLE_GENAI_MAX_RETRIES"), 3)
    if max_retries < 0:
        raise RuntimeError(f"Invalid GOOGLE_GENAI_MAX_RETRIES: {max_retries!r}. Expected a non-negative integer.")

    store_audit_content = _parse_bool(os.environ.get("STORE_LLM_AUDIT_CONTENT", "true"), True)

    return GeminiConfig(
        provider=provider,
        project=project,
        location=location,
        model=model,
        api_version=api_version,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        request_timeout_seconds=request_timeout_seconds,
        max_retries=max_retries,
        store_audit_content=store_audit_content,
    )


@dataclass(frozen=True)
class SQLiteConfig:
    """SQLite connection settings. See docs/TESTING_GEMINI_SQLITE_MIGRATION.md."""

    db_path: Path
    busy_timeout_ms: int
    journal_mode: str
    foreign_keys: bool
    synchronous: str
    debug: bool


def _build_sqlite_config() -> SQLiteConfig:
    database_backend = os.environ.get("DATABASE_BACKEND", "sqlite").strip().lower() or "sqlite"
    if database_backend not in VALID_DATABASE_BACKENDS:
        raise RuntimeError(
            f"Unsupported DATABASE_BACKEND: {database_backend!r}. Expected one of {sorted(VALID_DATABASE_BACKENDS)}."
        )

    raw_path = os.environ.get("SQLITE_DB_PATH", "data/dvp_meeting_prep.sqlite3").strip() or "data/dvp_meeting_prep.sqlite3"
    db_path = Path(raw_path)
    if not db_path.is_absolute():
        # Always relative to PROJECT_ROOT, never the current working
        # directory, for the same reason ENV_FILE resolution is: behavior
        # must not depend on where a script happens to be invoked from.
        db_path = PROJECT_ROOT / db_path

    busy_timeout_ms = _parse_int(os.environ.get("SQLITE_BUSY_TIMEOUT_MS"), 10000)
    if busy_timeout_ms < 0:
        raise RuntimeError(f"Invalid SQLITE_BUSY_TIMEOUT_MS: {busy_timeout_ms!r}. Expected a non-negative integer.")

    journal_mode = os.environ.get("SQLITE_JOURNAL_MODE", "WAL").strip().upper() or "WAL"
    if journal_mode not in VALID_SQLITE_JOURNAL_MODES:
        raise RuntimeError(
            f"Invalid SQLITE_JOURNAL_MODE: {journal_mode!r}. Expected one of {sorted(VALID_SQLITE_JOURNAL_MODES)}."
        )

    synchronous = os.environ.get("SQLITE_SYNCHRONOUS", "NORMAL").strip().upper() or "NORMAL"
    if synchronous not in VALID_SQLITE_SYNCHRONOUS_MODES:
        raise RuntimeError(
            f"Invalid SQLITE_SYNCHRONOUS: {synchronous!r}. Expected one of {sorted(VALID_SQLITE_SYNCHRONOUS_MODES)}."
        )

    return SQLiteConfig(
        db_path=db_path,
        busy_timeout_ms=busy_timeout_ms,
        journal_mode=journal_mode,
        foreign_keys=_parse_bool(os.environ.get("SQLITE_FOREIGN_KEYS", "true"), True),
        synchronous=synchronous,
        debug=_parse_bool(os.environ.get("SQLITE_DEBUG", "false"), False),
    )


@dataclass(frozen=True)
class Settings:
    database_backend: str
    sqlite: SQLiteConfig

    data_source: str
    app_env: str
    env_file_used: str
    csv_input_path: str | None
    salesforce: SalesforceConfig
    gemini: GeminiConfig


def _resolve_env_file_name() -> str:
    """Pick which dotenv file to load, before any dotenv has been loaded.

    Precedence: an explicit ENV_FILE wins outright; otherwise APP_ENV picks a
    conventional file name (sandbox -> .env.sandbox, production ->
    .env.production); otherwise fall back to plain .env. Both of these are
    read straight from the OS environment so they work even before any
    dotenv file has been loaded.
    """
    explicit = os.environ.get("ENV_FILE", "").strip()
    if explicit:
        return explicit
    app_env_hint = os.environ.get("APP_ENV", "").strip().lower()
    return {"sandbox": ".env.sandbox", "production": ".env.production"}.get(app_env_hint, ".env")


def _build_salesforce_config() -> SalesforceConfig:
    auth_mode = os.environ.get("SF_AUTH_MODE", "password").strip().lower() or "password"
    if auth_mode not in VALID_SF_AUTH_MODES:
        raise RuntimeError(f"Unsupported SF_AUTH_MODE: {auth_mode!r}. Expected one of {sorted(VALID_SF_AUTH_MODES)}.")

    activity_start_date = os.environ.get("SF_ACTIVITY_START_DATE", "").strip() or None
    if activity_start_date is not None:
        activity_start_date = validate_iso_date(activity_start_date)

    return SalesforceConfig(
        auth_mode=auth_mode,
        username=os.environ.get("SF_USERNAME", "").strip() or None,
        password=os.environ.get("SF_PASSWORD") or None,
        security_token=os.environ.get("SF_SECURITY_TOKEN") or None,
        access_token=os.environ.get("SF_ACCESS_TOKEN") or None,
        instance_url=os.environ.get("SF_INSTANCE_URL", "").strip() or None,
        advisor_object=os.environ.get("SF_ADVISOR_OBJECT", "Account").strip() or "Account",
        advisor_number_field=os.environ.get("SF_ADVISOR_NUMBER_FIELD", "Advisor_Number__c").strip()
        or "Advisor_Number__c",
        practice_lookup_field=os.environ.get("SF_PRACTICE_LOOKUP_FIELD", "").strip() or None,
        task_link_field=os.environ.get("SF_TASK_LINK_FIELD", "WhatId").strip() or "WhatId",
        opportunity_link_field=os.environ.get("SF_OPPORTUNITY_LINK_FIELD", "AccountId").strip() or "AccountId",
        advisor_numbers=_parse_csv_list(os.environ.get("SF_ADVISOR_NUMBERS", "17018,34318,34605,21114,20728")),
        task_subjects=_parse_csv_list(os.environ.get("SF_TASK_SUBJECTS", "Call,Virtual Meeting")),
        activity_start_date=activity_start_date,
        expected_advisor_count=_parse_int(os.environ.get("SF_EXPECTED_ADVISOR_COUNT"), 5),
        expected_task_count=_parse_int(os.environ.get("SF_EXPECTED_TASK_COUNT"), 83),
        expected_opportunity_count=_parse_int(os.environ.get("SF_EXPECTED_OPPORTUNITY_COUNT"), 4),
        advisor_extra_fields=_parse_csv_list(os.environ.get("SF_ADVISOR_EXTRA_FIELDS", "")),
        task_extra_fields=_parse_csv_list(os.environ.get("SF_TASK_EXTRA_FIELDS", "")),
        opportunity_extra_fields=_parse_csv_list(os.environ.get("SF_OPPORTUNITY_EXTRA_FIELDS", "")),
        advisor_field_map=_parse_field_map(os.environ.get("SF_ADVISOR_FIELD_MAP", ""), DEFAULT_ADVISOR_FIELD_MAP),
        task_field_map=_parse_field_map(os.environ.get("SF_TASK_FIELD_MAP", ""), DEFAULT_TASK_FIELD_MAP),
        debug=_parse_bool(os.environ.get("SF_DEBUG", "false"), False),
        debug_sample_size=_parse_int(os.environ.get("SF_DEBUG_SAMPLE_SIZE"), 3),
        save_debug_extracts=_parse_bool(os.environ.get("SF_SAVE_DEBUG_EXTRACTS", "false"), False),
        strict_expected_counts=_parse_bool(os.environ.get("SF_STRICT_EXPECTED_COUNTS", "false"), False),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # Precedence: real OS environment variables > the selected ENV_FILE > a
    # plain .env for anything not covered > hardcoded defaults below.
    # override=False on every call means an already-exported OS env var is
    # never clobbered by a dotenv file.
    # Deliberately always relative to PROJECT_ROOT, never the current working
    # directory -- a bare load_dotenv(override=False) falls back to searching
    # upward from CWD, which would make behavior depend on where a script
    # happens to be invoked from instead of the repo's actual .env files.
    env_file_name = _resolve_env_file_name()
    env_file_path = PROJECT_ROOT / env_file_name
    load_dotenv(env_file_path, override=False)
    if env_file_name != ".env":
        load_dotenv(PROJECT_ROOT / ".env", override=False)
    # Always printed (not gated behind LOG_LEVEL) -- which env file actually
    # won is a common source of confusion (APP_ENV/ENV_FILE picks .env vs
    # .env.sandbox vs .env.production; override=False means an
    # already-exported shell var always beats any of them) and every script
    # calls get_settings(), so this one line covers all of them for free.
    print(f"[CONFIG] Loaded env file: {env_file_path} (exists: {env_file_path.exists()})")

    data_source = os.environ.get("DATA_SOURCE", "salesforce").strip().lower() or "salesforce"
    if data_source not in VALID_DATA_SOURCES:
        raise RuntimeError(f"Unsupported DATA_SOURCE: {data_source!r}. Expected one of {sorted(VALID_DATA_SOURCES)}.")

    app_env = os.environ.get("APP_ENV", "sandbox").strip().lower() or "sandbox"
    if app_env not in VALID_APP_ENVS:
        raise RuntimeError(f"Unsupported APP_ENV: {app_env!r}. Expected one of {sorted(VALID_APP_ENVS)}.")

    database_backend = os.environ.get("DATABASE_BACKEND", "sqlite").strip().lower() or "sqlite"

    return Settings(
        database_backend=database_backend,
        sqlite=_build_sqlite_config(),
        data_source=data_source,
        app_env=app_env,
        env_file_used=env_file_name,
        csv_input_path=os.environ.get("CSV_INPUT_PATH", "").strip() or None,
        salesforce=_build_salesforce_config(),
        gemini=_build_gemini_config(),
    )


def configure_logging(level_name: str | None = None) -> None:
    """Configure the root logger from LOG_LEVEL (or an explicit override).

    Safe to call more than once -- re-applies the level to already-configured
    handlers instead of adding duplicate handlers.
    """
    resolved = (level_name or os.environ.get("LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, resolved, None)
    if not isinstance(level, int):
        raise RuntimeError(f"Unsupported LOG_LEVEL: {resolved!r}.")

    root = logging.getLogger()
    if root.handlers:
        root.setLevel(level)
    else:
        logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def repo_path(*parts: str) -> Path:
    return PROJECT_ROOT.joinpath(*parts)
