from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dvp_meeting_prep.config import configure_logging, get_settings
from dvp_meeting_prep.files import read_salesforce_rows
from dvp_meeting_prep.salesforce.extraction import run_extraction

# Advisor-level fields are joined from the Advisor record onto every Task row
# for that advisor, so they should be identical across all of an advisor's
# rows -- a straight value comparison (first row from each source) is
# meaningful here.
ADVISOR_LEVEL_FIELDS = [
    "advisor_name",
    "district_vp_wholesaling",
    "pwm",
    "book_size",
    "assets_under_management",
    "new_business_ytd",
    "area",
    "region_office_number",
    "assigned",
]

# Task-level fields vary per row -- row order/count can legitimately differ
# between a live extraction and a point-in-time manual export, so these are
# compared as sets rather than a single value.
TASK_LEVEL_FIELDS = [
    "subject",
    "task_subtype",
    "interaction_type",
    "status",
    "completed_date_time",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare live Salesforce sandbox data against a manually-provided legacy spreadsheet, "
        "advisor by advisor: which advisors match, and where their field values differ."
    )
    parser.add_argument(
        "legacy_file",
        help="Path to the manual Salesforce spreadsheet (.xlsx/.xlsm only -- re-save a .xlsb as .xlsx first; "
        "openpyxl cannot read .xlsb directly).",
    )
    return parser


def _index_by_advisor_number(rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        number = row.get("advisor_number")
        if number:
            grouped[str(number).strip()].append(row)
    return grouped


def _diff_advisor_fields(live_row: dict, legacy_row: dict) -> list[tuple[str, object, object]]:
    diffs = []
    for field in ADVISOR_LEVEL_FIELDS:
        live_value = live_row.get(field)
        legacy_value = legacy_row.get(field)
        if str(live_value or "").strip() != str(legacy_value or "").strip():
            diffs.append((field, live_value, legacy_value))
    return diffs


def _task_value_set(rows: list[dict], field: str) -> set[str]:
    return {str(row.get(field)).strip() for row in rows if row.get(field) not in (None, "")}


def main() -> int:
    args = build_parser().parse_args()
    configure_logging()
    settings = get_settings()

    legacy_path = Path(args.legacy_file)
    if not legacy_path.exists():
        print(f"[ERROR] Legacy file not found: {legacy_path}")
        return 2
    if legacy_path.suffix.lower() == ".xlsb":
        print(
            f"[ERROR] {legacy_path.name} is a .xlsb (Excel Binary Workbook) file -- openpyxl (used to parse "
            "this spreadsheet) cannot read that format. Open it in Excel and 'Save As' .xlsx, then pass that "
            "file instead."
        )
        return 2

    print(f"[LIVE] Connecting to Salesforce ({settings.app_env}) and pulling advisor/task data...")
    live_result = run_extraction(settings, dry_run=True)
    live_rows = live_result.legacy_rows
    live_by_advisor = _index_by_advisor_number(live_rows)
    print(f"[LIVE] {len(live_rows)} rows across {len(live_by_advisor)} advisor(s)")

    print(f"[LEGACY] Parsing {legacy_path}...")
    legacy_rows = read_salesforce_rows(legacy_path)
    legacy_by_advisor = _index_by_advisor_number(legacy_rows)
    print(f"[LEGACY] {len(legacy_rows)} rows across {len(legacy_by_advisor)} advisor(s)")

    matched = []
    not_in_legacy = []
    for advisor_number in sorted(live_by_advisor):
        if advisor_number in legacy_by_advisor:
            matched.append(advisor_number)
        else:
            not_in_legacy.append(advisor_number)

    print(f"\n{'=' * 70}")
    print(f"SUMMARY: {len(live_by_advisor)} advisor(s) in live Salesforce, {len(matched)} also found in the legacy file")
    print(f"{'=' * 70}\n")

    if not_in_legacy:
        print("[NOT IN LEGACY FILE]")
        for advisor_number in not_in_legacy:
            name = live_by_advisor[advisor_number][0].get("advisor_name")
            print(f"  {advisor_number}  {name}")
        print()

    for advisor_number in matched:
        live_advisor_rows = live_by_advisor[advisor_number]
        legacy_advisor_rows = legacy_by_advisor[advisor_number]
        name = live_advisor_rows[0].get("advisor_name")

        print(f"[MATCH] {advisor_number}  {name}")
        print(f"  rows: live={len(live_advisor_rows)}  legacy={len(legacy_advisor_rows)}")

        field_diffs = _diff_advisor_fields(live_advisor_rows[0], legacy_advisor_rows[0])
        if field_diffs:
            print("  advisor-level field differences:")
            for field, live_value, legacy_value in field_diffs:
                print(f"    {field}: live={live_value!r}  legacy={legacy_value!r}")
        else:
            print("  advisor-level fields: identical")

        for field in TASK_LEVEL_FIELDS:
            live_values = _task_value_set(live_advisor_rows, field)
            legacy_values = _task_value_set(legacy_advisor_rows, field)
            only_live = live_values - legacy_values
            only_legacy = legacy_values - live_values
            if only_live or only_legacy:
                print(f"  {field} -- only in live: {sorted(only_live)}  only in legacy: {sorted(only_legacy)}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
