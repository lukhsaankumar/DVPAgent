from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dvp_meeting_prep.config import configure_logging, get_settings
from dvp_meeting_prep.db import get_database
from dvp_meeting_prep.ingest import ingest_rows
from dvp_meeting_prep.salesforce import client as sf_client
from dvp_meeting_prep.salesforce import metadata as sf_metadata
from dvp_meeting_prep.salesforce.extraction import run_extraction


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Salesforce metadata discovery, dry-run validation, and full advisor/task extraction."
    )
    parser.add_argument(
        "--discover-salesforce",
        action="store_true",
        help="Connect to Salesforce and print a metadata discovery report (candidate objects/fields), then exit without querying advisor data.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Connect, query, normalize, and validate, but do not write anything to the database.",
    )
    parser.add_argument(
        "--source",
        choices=["salesforce", "csv"],
        default=None,
        help="Override DATA_SOURCE for this run only.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.source:
        os.environ["DATA_SOURCE"] = args.source

    configure_logging()
    settings = get_settings()

    if args.discover_salesforce:
        # Discovery always talks to Salesforce directly, regardless of the
        # configured DATA_SOURCE -- its purpose is to find the real object/
        # field API names *before* you switch DATA_SOURCE=salesforce.
        client = sf_client.connect(settings.salesforce, settings.app_env)
        sf_metadata.run_discovery(client, settings.salesforce)
        return

    if settings.data_source != "salesforce":
        print(f"[CONFIG] Data source: {settings.data_source} -- nothing to extract from Salesforce.")
        print("Pass --source salesforce (or set DATA_SOURCE=salesforce) to run a Salesforce extraction.")
        return

    result = run_extraction(settings, dry_run=args.dry_run)

    # Always the live table: this script only ever runs a real Salesforce
    # extraction (never the csv fallback, which writes to salesforce_data via
    # scripts/ingest_all.py instead) -- see db.SALESFORCE_TABLE_BY_ADVISOR_SOURCE_MODE.
    target_table = "salesforce_data_auto"

    if args.dry_run:
        print(f"\n[DRY RUN] Would ingest {len(result.legacy_rows)} rows into {target_table} (nothing was written).")
        return

    database = get_database()
    database.ensure_schema_ready()
    inserted = ingest_rows(database, target_table, result.legacy_rows, replace_existing=True)
    print(f"{target_table}: {inserted} rows ingested")


if __name__ == "__main__":
    main()
