from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Windows PowerShell consoles often default stdout to the system codepage
# rather than UTF-8, which garbles accented characters. Real advisor/company
# data can contain these, so make the console safe for them.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dvp_meeting_prep.config import configure_logging, get_settings
from dvp_meeting_prep.files import read_salesforce_rows
from dvp_meeting_prep.salesforce import client as sf_client
from dvp_meeting_prep.salesforce import metadata as sf_metadata
from dvp_meeting_prep.salesforce.client import with_retries

# Candidate fields observed during --discover-salesforce that could plausibly
# be the real per-advisor join key, beyond the one currently configured in
# SF_ADVISOR_NUMBER_FIELD. Labels are what Salesforce reported for each.
DEFAULT_CANDIDATE_FIELDS = [
    "Servicing_Advisor_Number__c",  # "Servicing Advisor Number" -- currently configured
    "Servicing_Advisors__c",  # "Consultant Number"
    "NEXJ_CONSULTANTNUMBER__c",  # "NEXJ CONSULTANTNUMBER" -- likely a legacy-CRM-migrated number
    "AccountNumber",  # "Contact's Account Number"
    "Practice_Region_Number__c",  # "Practice Region Number" -- unlikely, but cheap to rule out
]

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Test multiple candidate Salesforce fields against the real legacy spreadsheet's advisor "
        "numbers to find which one (if any) is the actual join key -- useful when the currently configured "
        "SF_ADVISOR_NUMBER_FIELD produces little/no overlap and someone with knowledge of the org says real "
        "overlap should exist."
    )
    parser.add_argument("legacy_file", help="Path to the manual Salesforce spreadsheet (.xlsx/.xlsm only).")
    parser.add_argument(
        "--fields",
        default=None,
        help=f"Comma-separated field API names to test instead of the built-in defaults "
        f"({','.join(DEFAULT_CANDIDATE_FIELDS)}).",
    )
    parser.add_argument(
        "--also-check-names",
        action="store_true",
        help="Also compute name-based overlap (Account.Name vs the legacy file's advisor_name), independent of "
        "any number field -- useful to tell 'wrong field' apart from 'these genuinely aren't the same people'.",
    )
    return parser


def _query_distinct_field_values(client, object_name: str, field_name: str) -> dict[str, set[str]]:
    """Returns {field_value: {account_names_sharing_that_value}} for every
    non-null value of `field_name` on `object_name`."""
    soql = f"SELECT Id, Name, {field_name} FROM {object_name} WHERE {field_name} != null"
    result = with_retries(lambda: client.query_all(soql), step_name=f"probe_{field_name}")
    records = list(result.get("records", []))
    grouped: dict[str, set[str]] = {}
    for record in records:
        value = str(record.get(field_name) or "").strip()
        if not value:
            continue
        grouped.setdefault(value, set()).add(str(record.get("Name") or ""))
    return grouped


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
        print(f"[ERROR] {legacy_path.name} is .xlsb -- re-save as .xlsx first (openpyxl can't read .xlsb).")
        return 2

    candidate_fields = [f.strip() for f in args.fields.split(",")] if args.fields else list(DEFAULT_CANDIDATE_FIELDS)
    for field in candidate_fields:
        if not _IDENTIFIER_RE.match(field):
            print(f"[ERROR] {field!r} is not a valid Salesforce field API name.")
            return 2

    print(f"[LEGACY] Parsing {legacy_path}...")
    legacy_rows = read_salesforce_rows(legacy_path)
    legacy_numbers = {str(r.get("advisor_number") or "").strip() for r in legacy_rows if r.get("advisor_number")}
    legacy_names_casefold = {str(r.get("advisor_name") or "").strip().casefold() for r in legacy_rows if r.get("advisor_name")}
    print(f"[LEGACY] {len(legacy_rows)} rows, {len(legacy_numbers)} distinct advisor numbers, {len(legacy_names_casefold)} distinct advisor names")

    print(f"\n[LIVE] Connecting to Salesforce ({settings.app_env})...")
    client = sf_client.connect(sf_config, settings.app_env)
    described = sf_metadata.describe_object(client, sf_config.advisor_object)
    field_names_on_object = set(sf_metadata.describe_field_names(described).keys())

    print(f"\n{'=' * 78}")
    print(f"{'FIELD':<32} {'NON-NULL':>10} {'DISTINCT':>10} {'OVERLAP':>9} {'OVERLAP %':>10}")
    print(f"{'=' * 78}")

    results = []
    for field in candidate_fields:
        if field not in field_names_on_object:
            print(f"{field:<32} {'(not a field on ' + sf_config.advisor_object + ')':>42}")
            continue
        grouped = _query_distinct_field_values(client, sf_config.advisor_object, field)
        overlap = set(grouped) & legacy_numbers
        total_records = sum(len(names) for names in grouped.values())
        pct = (len(overlap) / len(legacy_numbers) * 100) if legacy_numbers else 0.0
        print(f"{field:<32} {total_records:>10} {len(grouped):>10} {len(overlap):>9} {pct:>9.1f}%")
        results.append((field, overlap, grouped))

    print(f"{'=' * 78}\n")

    best_field, best_overlap, best_grouped = max(results, key=lambda r: len(r[1]), default=(None, set(), {}))
    if best_field and best_overlap:
        print(f"[BEST MATCH] {best_field} has the highest overlap: {len(best_overlap)} advisor number(s) in common.")
        print("[BEST MATCH] Sample overlapping values:")
        for value in sorted(best_overlap)[:15]:
            print(f"    {value}  ->  {sorted(best_grouped[value])}")
    else:
        print("[RESULT] None of the tested fields produced any overlap with the legacy file's advisor numbers.")

    if args.also_check_names:
        print(f"\n{'=' * 78}")
        print("[NAME CHECK] Account.Name vs legacy advisor_name (case-insensitive, independent of any number field)")
        print(f"{'=' * 78}")
        soql = f"SELECT Id, Name FROM {sf_config.advisor_object} WHERE Name != null"
        result = with_retries(lambda: client.query_all(soql), step_name="probe_names")
        live_names_casefold = {str(r.get("Name") or "").strip().casefold(): r.get("Name") for r in result.get("records", [])}
        name_overlap = set(live_names_casefold) & legacy_names_casefold
        print(f"[NAME CHECK] {len(live_names_casefold)} distinct live names, {len(name_overlap)} overlap with the legacy file by name")
        if name_overlap:
            print("[NAME CHECK] Sample overlapping names:")
            for key in sorted(name_overlap)[:15]:
                print(f"    {live_names_casefold[key]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
