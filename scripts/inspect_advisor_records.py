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
    "James Carney": "20728",
}

# Lookup fields on the Account record worth following to a related User
# record and checking there too -- every Account dumped so far has both
# populated, and "Field_User__c" (paired with Field_User_Persona__c='Advisor')
# strongly suggests the actual advisor identity/number lives on that User,
# not the Account (which looks like it represents the practice/book of
# business instead).
RELATED_USER_LOOKUP_FIELDS = ["Field_User__c", "OwnerId"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pull the full Account record (and its related User, via Field_User__c/OwnerId) for specific "
        "named advisors, and search every field on both for the expected advisor number -- for when filtering by "
        "a specific number field returns nothing, to find out where the number actually lives rather than "
        "guessing at more field names."
    )
    parser.add_argument(
        "--pairs",
        default=None,
        help='Override the built-in name=number list, comma-separated, e.g. "Scott Syrja=17018,Mathis Turcotte=34318".',
    )
    parser.add_argument(
        "--skip-users",
        action="store_true",
        help="Only check the Account record, don't follow Field_User__c/OwnerId to the related User record.",
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


def _fields_matching(record: dict, expected_number: str) -> list[str]:
    matches = []
    for field_name, value in record.items():
        if field_name in ("attributes",) or value is None:
            continue
        if str(value).strip() == expected_number:
            matches.append(field_name)
    return matches


def _dump_non_empty_fields(record: dict) -> None:
    for field_name, value in sorted(record.items()):
        if field_name in ("attributes",) or value in (None, ""):
            continue
        print(f"    {field_name}: {value!r}")


def _check_record(client, object_name: str, record_id: str, expected_number: str, label: str) -> bool:
    """Fetch one record by Id, search it for expected_number, print the
    result. Returns True if found."""
    sobject = getattr(client, object_name)
    try:
        full_record = with_retries(lambda: sobject.get(record_id), step_name=f"get_{object_name}")
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] Could not fetch {label} ({object_name} {record_id}): {exc}")
        return False

    matches = _fields_matching(full_record, expected_number)
    if matches:
        print(f"[FOUND] Number {expected_number!r} found on {label} in field(s): {matches}")
        return True

    print(f"[NOT FOUND] Number {expected_number!r} not on {label}.")
    print(f"[DUMP] Non-empty fields on {label}:")
    _dump_non_empty_fields(full_record)
    return False


def main() -> int:
    args = build_parser().parse_args()
    configure_logging()
    settings = get_settings()
    sf_config = settings.salesforce
    pairs = _parse_pairs(args.pairs)

    print(f"[LIVE] Connecting to Salesforce ({settings.app_env})...")
    client = sf_client.connect(sf_config, settings.app_env)

    for name, expected_number in pairs.items():
        print(f"\n{'=' * 78}")
        print(f"[SEARCH] {sf_config.advisor_object}.Name = {name!r} (expecting number {expected_number!r} somewhere)")
        print(f"{'=' * 78}")

        # Small, safe query -- just Id/Name (+ the related-User lookup
        # fields), never every field, so this never risks the "URL/header
        # too large" error a full-field SELECT can hit on an object with
        # 100+ fields (as this one has).
        lookup_fields = ", ".join(RELATED_USER_LOOKUP_FIELDS) if not args.skip_users else ""
        select_fields = f"Id, Name{', ' + lookup_fields if lookup_fields else ''}"
        soql = format_soql(
            f"SELECT {select_fields} FROM {sf_config.advisor_object} WHERE Name = {{name}}", name=name
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
            print(f"\n[RECORD] {sf_config.advisor_object} Id={record_id}")

            found = _check_record(client, sf_config.advisor_object, record_id, expected_number, "the Account record")
            if found or args.skip_users:
                continue

            checked_user_ids: set[str] = set()
            for lookup_field in RELATED_USER_LOOKUP_FIELDS:
                user_id = stub.get(lookup_field)
                if not user_id or user_id in checked_user_ids:
                    continue
                checked_user_ids.add(user_id)
                print(f"\n[FOLLOW] {lookup_field} -> User {user_id}")
                if _check_record(client, "User", user_id, expected_number, f"the related User ({lookup_field})"):
                    break

    return 0


if __name__ == "__main__":
    sys.exit(main())
