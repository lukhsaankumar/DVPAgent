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


def normalize_supabase_url(url: str) -> str:
    cleaned = url.strip()
    cleaned = cleaned.removesuffix("/")
    cleaned = cleaned.removesuffix("/rest/v1")
    return cleaned


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
class Settings:
    supabase_url: str
    supabase_secret_key: str
    supabase_publishable_key: str | None
    openai_api_key: str
    openai_model: str

    data_source: str
    app_env: str
    env_file_used: str
    csv_input_path: str | None
    salesforce: SalesforceConfig

    @property
    def supabase_project_url(self) -> str:
        return normalize_supabase_url(self.supabase_url)


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
    load_dotenv(PROJECT_ROOT / env_file_name, override=False)
    if env_file_name != ".env":
        load_dotenv(PROJECT_ROOT / ".env", override=False)

    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    supabase_secret_key = os.environ.get("SUPABASE_SECRET_KEY", "").strip()
    supabase_publishable_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "").strip() or None
    openai_api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    openai_model = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini"

    if not supabase_url:
        raise RuntimeError("SUPABASE_URL is missing from the environment.")
    if not supabase_secret_key:
        raise RuntimeError("SUPABASE_SECRET_KEY is missing from the environment.")
    if not openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is missing from the environment.")

    data_source = os.environ.get("DATA_SOURCE", "salesforce").strip().lower() or "salesforce"
    if data_source not in VALID_DATA_SOURCES:
        raise RuntimeError(f"Unsupported DATA_SOURCE: {data_source!r}. Expected one of {sorted(VALID_DATA_SOURCES)}.")

    app_env = os.environ.get("APP_ENV", "sandbox").strip().lower() or "sandbox"
    if app_env not in VALID_APP_ENVS:
        raise RuntimeError(f"Unsupported APP_ENV: {app_env!r}. Expected one of {sorted(VALID_APP_ENVS)}.")

    return Settings(
        supabase_url=supabase_url,
        supabase_secret_key=supabase_secret_key,
        supabase_publishable_key=supabase_publishable_key,
        openai_api_key=openai_api_key,
        openai_model=openai_model,
        data_source=data_source,
        app_env=app_env,
        env_file_used=env_file_name,
        csv_input_path=os.environ.get("CSV_INPUT_PATH", "").strip() or None,
        salesforce=_build_salesforce_config(),
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
