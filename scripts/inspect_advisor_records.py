from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from simple_salesforce import format_soql

from dvp_meeting_prep.config import configure_logging, get_settings
from dvp_meeting_prep.salesforce import client as sf_client
from dvp_meeting_prep.salesforce.client import with_retries

# name -> the number it's expected to correspond to, per what's actually
# known about this sandbox (not a guess).
DEFAULT_EXPECTED = {
    "Scott Syrja": "17018",
    "Mathis Turcotte": "34318",
    "Tamar Eisenberg": "34605",
    "Martin Leroux": "21114",
    "Olivier Champagne": "20728",
    "James Carney": "20728",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pull the full Account record for specific named advisors and search every field for the "
        "expected advisor number -- for when filtering by a specific number field returns nothing, to find out "
        "whether the number lives in a different field (or nowhere at all) rather than guessing at more field names."
    )
    parser.add_argument(
        "--pairs",
        default=None,
        help='Override the built-in name=number list, comma-separated, e.g. "Scott Syrja=17018,Mathis Turcotte=34318".',
    )
    return parser


def _parse_pairs(raw: str | None) -> dict[str, str]:
    if not raw:
        return DEFAULT_EXPECTED
    pairs: dict[str, str] = {}
    for chunk in raw.split(","):
        name, _, number = chunk.strip().partition("=")
        if name and number:
            pairs[name.strip()] = number.strip()
    return pairs


def main() -> int:
    args = build_parser().parse_args()
    configure_logging()
    settings = get_settings()
    sf_config = settings.salesforce
    pairs = _parse_pairs(args.pairs)

    print(f"[LIVE] Connecting to Salesforce ({settings.app_env})...")
    client = sf_client.connect(sf_config, settings.app_env)
    sobject = getattr(client, sf_config.advisor_object)

    for name, expected_number in pairs.items():
        print(f"\n{'=' * 78}")
        print(f"[SEARCH] {sf_config.advisor_object}.Name = {name!r} (expecting number {expected_number!r} somewhere)")
        print(f"{'=' * 78}")

        # Small, safe query -- just Id/Name, never every field, so this
        # never risks the "URL/header too large" error a full-field SELECT
        # can hit on an object with 100+ fields (as this one has).
        soql = format_soql(
            f"SELECT Id, Name FROM {sf_config.advisor_object} WHERE Name = {{name}}", name=name
        )
        try:
            result = with_retries(lambda soql=soql: client.query(soql), step_name="find_by_name")
        except Exception as exc:  # noqa: BLE001 -- report and continue to the next name
            print(f"[ERROR] Query failed: {exc}")
            continue

        records = list(result.get("records", []))
        if not records:
            print(f"[NOT FOUND] No {sf_config.advisor_object} record with Name = {name!r} at all.")
            continue

        for stub in records:
            record_id = stub.get("Id")
            print(f"\n[RECORD] Id={record_id}")

            # Fetch every field on this one record via GET .../{object}/{id}
            # -- no field list in the URL at all, so this can't hit the same
            # 431 that a full-field SELECT did.
            try:
                full_record = with_retries(
                    lambda record_id=record_id: sobject.get(record_id), step_name="get_full_record"
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[ERROR] Could not fetch full record {record_id}: {exc}")
                continue

            matches = []
            for field_name, value in full_record.items():
                if field_name in ("attributes",) or value is None:
                    continue
                if str(value).strip() == expected_number:
                    matches.append(field_name)

            if matches:
                print(f"[FOUND] Number {expected_number!r} found in field(s): {matches}")
            else:
                print(f"[NOT FOUND] Number {expected_number!r} does not appear in any field on this record.")
                print("[DUMP] Non-empty fields on this record:")
                for field_name, value in sorted(full_record.items()):
                    if field_name in ("attributes",) or value in (None, ""):
                        continue
                    print(f"    {field_name}: {value!r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
