from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import json
import logging

from ..config import PROJECT_ROOT, Settings, get_settings
from . import client as sf_client
from . import metadata
from . import normalize as sf_normalize
from . import queries as sf_queries
from . import validate as sf_validate

logger = logging.getLogger("dvp_meeting_prep.salesforce")

# Free-text fields that may contain sensitive advisor/client notes -- never
# written to a debug extract file even when SF_SAVE_DEBUG_EXTRACTS=true, and
# never included in log/print output anywhere in this package.
_FREE_TEXT_FIELDS = {"Description", "NextStep", "comments"}


@dataclass
class SalesforceExtractionResult:
    legacy_rows: list[dict[str, Any]] = field(default_factory=list)
    advisors: list[dict[str, Any]] = field(default_factory=list)
    tasks: list[dict[str, Any]] = field(default_factory=list)
    opportunities: list[dict[str, Any]] = field(default_factory=list)
    scope: sf_queries.ScopeResolution | None = None
    normalization: sf_normalize.NormalizationResult | None = None
    validation: sf_validate.ValidationReport | None = None


def run_extraction(settings: Settings | None = None, *, dry_run: bool = False) -> SalesforceExtractionResult:
    """Run the full Salesforce extraction pipeline (connect -> resolve
    advisors -> tasks -> opportunities -> normalize -> validate) and return
    the result. Never writes to Supabase itself -- the caller decides what to
    do with `.legacy_rows` (e.g. scripts/ingest_all.py inserts them into
    salesforce_data), which is what makes `dry_run` safe: it only changes the
    final message printed, not what this function does.
    """
    settings = settings or get_settings()
    sf_config = settings.salesforce

    print("[START] Loading application configuration")
    print("[CONFIG] Data source: salesforce")
    print(f"[CONFIG] Environment: {settings.app_env}")
    print(f"[CONFIG] Authentication mode: {sf_config.auth_mode}")
    print(f"[CONFIG] Advisor count requested: {len(sf_config.advisor_numbers)}")
    logger.info(
        "[CONFIG] env_file=%s data_source=%s app_env=%s advisor_object=%s",
        settings.env_file_used,
        settings.data_source,
        settings.app_env,
        sf_config.advisor_object,
    )

    client = sf_client.connect(sf_config, settings.app_env)

    print("[METADATA] Validating configured objects and fields")
    advisor_described = metadata.describe_object(client, sf_config.advisor_object)
    task_described = metadata.describe_object(client, "Task")
    opportunity_described = metadata.describe_object(client, "Opportunity")
    print("[METADATA] Configuration validation passed")

    advisor_records = sf_queries.fetch_advisors(client, advisor_described, sf_config)
    scope = sf_queries.resolve_scope(advisor_records, sf_config)

    if sf_config.debug and scope.scope_ids:
        sf_queries.audit_task_subjects(client, scope.scope_ids, sf_config)

    if scope.scope_ids:
        task_records = sf_queries.fetch_tasks(client, task_described, scope.scope_ids, sf_config)
        opportunity_records = sf_queries.fetch_opportunities(client, opportunity_described, scope.scope_ids, sf_config)
    else:
        logger.warning("[TASKS] No advisor/practice scope resolved -- skipping Task and Opportunity queries.")
        print("[TASKS] Skipped: no advisors resolved to a relationship scope")
        print("[OPPORTUNITIES] Skipped: no advisors resolved to a relationship scope")
        task_records = []
        opportunity_records = []

    print("[NORMALIZE] Normalizing Salesforce records")
    print("[JOIN] Mapping Tasks and Opportunities to Advisors")
    normalization = sf_normalize.build_legacy_rows(advisor_records, task_records, scope, sf_config)
    opp_matched, opp_unmatched = sf_normalize.match_opportunities_to_scope(opportunity_records, scope, sf_config)
    logger.info(
        "[JOIN] advisor_to_opportunity_matches=%d unmatched_opportunity_scope_ids=%s",
        opp_matched,
        opp_unmatched,
    )

    validation = sf_validate.run_validation(
        scope=scope,
        task_records=task_records,
        opportunity_records=opportunity_records,
        normalization=normalization,
        sf_config=sf_config,
    )

    if sf_config.save_debug_extracts:
        _save_debug_extracts(advisor_records, task_records, opportunity_records)

    result = SalesforceExtractionResult(
        legacy_rows=normalization.legacy_rows,
        advisors=sf_normalize.normalize_records(advisor_records),
        tasks=sf_normalize.normalize_records(task_records),
        opportunities=sf_normalize.normalize_records(opportunity_records),
        scope=scope,
        normalization=normalization,
        validation=validation,
    )

    if dry_run:
        print("[COMPLETE] Salesforce dry run completed successfully (no data was written downstream)")
    else:
        print("[COMPLETE] Salesforce extraction completed successfully")
    return result


def _redact_notes(record: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in record.items() if k not in _FREE_TEXT_FIELDS}


def _save_debug_extracts(
    advisor_records: list[dict[str, Any]],
    task_records: list[dict[str, Any]],
    opportunity_records: list[dict[str, Any]],
) -> None:
    debug_dir = PROJECT_ROOT / ".salesforce_debug"
    debug_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = debug_dir / f"extract_{stamp}.json"

    payload = {
        "warning": "May contain sensitive advisor/client data. Free-text note fields (Description, NextStep, comments) are redacted. This directory is gitignored -- do not share these files.",
        "advisors": [_redact_notes(sf_normalize.strip_attributes(r)) for r in advisor_records],
        "tasks": [_redact_notes(sf_normalize.strip_attributes(r)) for r in task_records],
        "opportunities": [_redact_notes(sf_normalize.strip_attributes(r)) for r in opportunity_records],
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    logger.warning("[DEBUG] Wrote debug extract to %s (SF_SAVE_DEBUG_EXTRACTS=true) -- may contain sensitive data", out_path)
    print(f"[DEBUG] Wrote debug extract to {out_path} (redacted free-text fields; gitignored directory)")
