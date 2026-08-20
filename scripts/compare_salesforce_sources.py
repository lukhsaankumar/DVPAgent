from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Windows PowerShell consoles often default stdout to the system codepage
# rather than UTF-8, which garbles accented characters (e.g. French advisor
# names) into '?'/'�'. Real advisor/company data can contain these, so make
# the console safe for them -- the JSON dump file already writes UTF-8
# regardless, this only affects what prints to the terminal.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dvp_meeting_prep.config import PROJECT_ROOT, configure_logging, get_settings
from dvp_meeting_prep.files import read_salesforce_rows
from dvp_meeting_prep.salesforce import client as sf_client
from dvp_meeting_prep.salesforce import metadata as sf_metadata
from dvp_meeting_prep.salesforce import queries as sf_queries

# Advisor-level fields are joined from the Advisor record onto every Task row
# for that advisor in the normal ingest path, so they should be identical
# across all of an advisor's rows -- a straight value comparison is
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pull every advisor record from the live Salesforce sandbox (not just a fixed list of "
        "advisor numbers), print all of it, and compare against a manually-provided legacy spreadsheet for "
        "overlap and field differences."
    )
    parser.add_argument(
        "legacy_file",
        help="Path to the manual Salesforce spreadsheet (.xlsx/.xlsm only -- re-save a .xlsb as .xlsx first; "
        "openpyxl cannot read .xlsb directly).",
    )
    parser.add_argument(
        "--dump-file",
        default=None,
        help="Where to write the full raw Salesforce pull as JSON (default: "
        ".salesforce_debug/compare_pull_<timestamp>.json, gitignored).",
    )
    return parser


def _relationship_value(record: dict, dotted_field: str):
    """Resolve a possibly-dotted relationship field (e.g. 'Owner.Name')
    against a raw Salesforce record, which simple_salesforce returns as a
    nested dict for relationship fields."""
    value = record
    for part in dotted_field.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _map_to_legacy_shape(record: dict, advisor_number_field: str, advisor_field_map: dict[str, str]) -> dict:
    """Map a raw Account/advisor record into the same legacy-row column
    names used by files.read_salesforce_rows(), using the configured
    advisor_field_map so both sides of the comparison speak the same
    vocabulary."""
    mapped = {
        "advisor_name": record.get("Name"),
        "advisor_number": record.get(advisor_number_field),
    }
    for legacy_column, real_field in advisor_field_map.items():
        mapped[legacy_column] = _relationship_value(record, real_field)
    return mapped


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


def main() -> int:
    args = build_parser().parse_args()
    configure_logging()
    settings = get_settings()
    sf_config = settings.salesforce

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

    print(f"[LIVE] Connecting to Salesforce ({settings.app_env})...")
    client = sf_client.connect(sf_config, settings.app_env)
    advisor_described = sf_metadata.describe_object(client, sf_config.advisor_object)

    print("[LIVE] Pulling EVERY advisor record with a populated advisor number (unscoped -- not just SF_ADVISOR_NUMBERS)...")
    raw_records = sf_queries.fetch_all_advisors(client, advisor_described, sf_config)

    print(f"\n{'=' * 70}")
    print(f"[LIVE] Full pull: {len(raw_records)} advisor record(s) from {sf_config.advisor_object}")
    print(f"{'=' * 70}")
    live_rows = [_map_to_legacy_shape(r, sf_config.advisor_number_field, sf_config.advisor_field_map) for r in raw_records]
    for row in sorted(live_rows, key=lambda r: str(r.get("advisor_number") or "")):
        print(f"  {row.get('advisor_number')!s:>12}  {row.get('advisor_name')}")
        for field in ADVISOR_LEVEL_FIELDS[1:]:  # skip advisor_name, already printed
            value = row.get(field)
            if value not in (None, ""):
                print(f"      {field}: {value}")

    dump_path = Path(args.dump_file) if args.dump_file else None
    if dump_path is None:
        debug_dir = PROJECT_ROOT / ".salesforce_debug"
        debug_dir.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dump_path = debug_dir / f"compare_pull_{stamp}.json"
    dump_path.parent.mkdir(parents=True, exist_ok=True)
    dump_path.write_text(json.dumps(live_rows, indent=2, default=str), encoding="utf-8")
    print(f"\n[LIVE] Full pull also written to {dump_path} (gitignored -- may contain real advisor data)")

    print(f"\n[LEGACY] Parsing {legacy_path}...")
    legacy_rows = read_salesforce_rows(legacy_path)
    live_by_advisor = _index_by_advisor_number(live_rows)
    legacy_by_advisor = _index_by_advisor_number(legacy_rows)
    print(f"[LEGACY] {len(legacy_rows)} rows across {len(legacy_by_advisor)} advisor(s)")

    matched = []
    not_in_legacy = []
    for advisor_number in sorted(live_by_advisor):
        if advisor_number in legacy_by_advisor:
            matched.append(advisor_number)
        else:
            not_in_legacy.append(advisor_number)
    not_in_live = sorted(n for n in legacy_by_advisor if n not in live_by_advisor)

    print(f"\n{'=' * 70}")
    print(
        f"SUMMARY: {len(live_by_advisor)} advisor(s) live in Salesforce, {len(legacy_by_advisor)} advisor(s) "
        f"in the legacy file, {len(matched)} overlap"
    )
    print(f"{'=' * 70}\n")

    if matched:
        print("[OVERLAP -- in both Salesforce and the legacy file]")
        for advisor_number in matched:
            name = live_by_advisor[advisor_number][0].get("advisor_name")
            print(f"  {advisor_number}  {name}")
        print()

    if not_in_legacy:
        print(f"[IN SALESFORCE, NOT IN LEGACY FILE] ({len(not_in_legacy)})")
        for advisor_number in not_in_legacy[:50]:
            name = live_by_advisor[advisor_number][0].get("advisor_name")
            print(f"  {advisor_number}  {name}")
        if len(not_in_legacy) > 50:
            print(f"  ... and {len(not_in_legacy) - 50} more (see the full dump file for the complete list)")
        print()

    if not_in_live:
        print(f"[IN LEGACY FILE, NOT IN SALESFORCE] ({len(not_in_live)})")
        for advisor_number in not_in_live[:50]:
            name = legacy_by_advisor[advisor_number][0].get("advisor_name")
            print(f"  {advisor_number}  {name}")
        if len(not_in_live) > 50:
            print(f"  ... and {len(not_in_live) - 50} more")
        print()

    for advisor_number in matched:
        live_row = live_by_advisor[advisor_number][0]
        legacy_row = legacy_by_advisor[advisor_number][0]
        field_diffs = _diff_advisor_fields(live_row, legacy_row)
        if field_diffs:
            print(f"[FIELD DIFFERENCES] {advisor_number} {live_row.get('advisor_name')}")
            for field, live_value, legacy_value in field_diffs:
                print(f"    {field}: live={live_value!r}  legacy={legacy_value!r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
