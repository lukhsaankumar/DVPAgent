from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import logging

from ..config import SalesforceConfig
from .errors import SalesforceValidationError
from .normalize import LEGACY_ROW_COLUMNS, NormalizationResult, find_duplicate_ids
from .queries import ScopeResolution

logger = logging.getLogger("dvp_meeting_prep.salesforce")


@dataclass
class CountCheck:
    label: str
    expected: int
    actual: int

    @property
    def passed(self) -> bool:
        return self.expected == self.actual


@dataclass
class ValidationReport:
    count_checks: list[CountCheck] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.problems


def _check_count(report: ValidationReport, label: str, expected: int, actual: int) -> None:
    check = CountCheck(label, expected, actual)
    report.count_checks.append(check)
    status = "PASS" if check.passed else "MISMATCH"
    line = f"[VALIDATION] {label}: expected={expected} actual={actual} status={status}"
    print(line)
    if check.passed:
        logger.info(line)
    else:
        logger.warning(line)
        report.problems.append(f"{label} count mismatch: expected {expected}, got {actual}")


def run_validation(
    *,
    scope: ScopeResolution,
    task_records: list[dict[str, Any]],
    opportunity_records: list[dict[str, Any]],
    normalization: NormalizationResult,
    sf_config: SalesforceConfig,
) -> ValidationReport:
    """Run every expected-count and integrity check described in the
    extraction spec, then either warn (SF_STRICT_EXPECTED_COUNTS=false, the
    default) or raise (=true).
    """
    print("[VALIDATION] Running extraction checks")
    report = ValidationReport()

    _check_count(report, "Advisors", sf_config.expected_advisor_count, len(scope.number_to_advisor))
    _check_count(report, "Tasks", sf_config.expected_task_count, len(task_records))
    _check_count(report, "Opportunities", sf_config.expected_opportunity_count, len(opportunity_records))

    if scope.missing_numbers:
        msg = (
            f"Advisor {sf_config.advisor_lookup_field} values requested (SF_ADVISOR_LOOKUP_VALUES) "
            f"but not found in Salesforce: {scope.missing_numbers}"
        )
        logger.warning("[VALIDATION] %s", msg)
        report.problems.append(msg)

    if scope.duplicate_numbers:
        msg = f"Duplicate Advisor number mappings: {scope.duplicate_numbers}"
        logger.warning("[VALIDATION] %s", msg)
        report.problems.append(msg)

    duplicate_task_ids = find_duplicate_ids(task_records)
    if duplicate_task_ids:
        msg = f"Duplicate Task IDs returned: {duplicate_task_ids}"
        logger.warning("[VALIDATION] %s", msg)
        report.problems.append(msg)

    duplicate_opportunity_ids = find_duplicate_ids(opportunity_records)
    if duplicate_opportunity_ids:
        msg = f"Duplicate Opportunity IDs returned: {duplicate_opportunity_ids}"
        logger.warning("[VALIDATION] %s", msg)
        report.problems.append(msg)

    allowed_subjects = set(sf_config.task_subjects)
    bad_subjects = sorted({t.get("Subject") for t in task_records if t.get("Subject") not in allowed_subjects})
    if bad_subjects:
        msg = f"Tasks returned with unexpected Subject values (outside SF_TASK_SUBJECTS): {bad_subjects}"
        logger.warning("[VALIDATION] %s", msg)
        report.problems.append(msg)

    known_scope_ids = set(scope.number_to_scope_id.values())
    tasks_outside_scope = [
        t.get("Id") for t in task_records if t.get(sf_config.task_link_field) not in known_scope_ids
    ]
    if tasks_outside_scope:
        msg = f"Tasks returned that are outside the resolved advisor/practice scope: {tasks_outside_scope[:10]}"
        logger.warning("[VALIDATION] %s", msg)
        report.problems.append(msg)

    opportunities_outside_scope = [
        o.get("Id") for o in opportunity_records if o.get(sf_config.opportunity_link_field) not in known_scope_ids
    ]
    if opportunities_outside_scope:
        msg = f"Opportunities returned that are outside the resolved advisor/practice scope: {opportunities_outside_scope[:10]}"
        logger.warning("[VALIDATION] %s", msg)
        report.problems.append(msg)

    if normalization.legacy_rows:
        actual_columns = set(normalization.legacy_rows[0].keys())
        expected_columns = set(LEGACY_ROW_COLUMNS)
        if actual_columns != expected_columns:
            msg = f"Normalized row schema does not match the legacy contract. Missing={expected_columns - actual_columns} Extra={actual_columns - expected_columns}"
            logger.warning("[VALIDATION] %s", msg)
            report.problems.append(msg)

    if report.passed:
        print("[VALIDATION] All checks passed")
    elif sf_config.strict_expected_counts:
        detail = "; ".join(report.problems)
        raise SalesforceValidationError(f"Salesforce extraction failed strict validation: {detail}")
    else:
        print(f"[VALIDATION] {len(report.problems)} issue(s) found (SF_STRICT_EXPECTED_COUNTS=false, continuing)")

    return report
