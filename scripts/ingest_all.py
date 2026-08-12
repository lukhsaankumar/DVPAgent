from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dvp_meeting_prep.config import repo_path
from dvp_meeting_prep.db import get_database
from dvp_meeting_prep.ingest import ingest_all_sources


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest Salesforce, Tableau, and consultant scorecard data into SQLite.")
    parser.add_argument(
        "--source",
        choices=["salesforce", "csv"],
        default=None,
        help="Override DATA_SOURCE for the Salesforce/advisor portion of ingestion (default: use the DATA_SOURCE env var, which defaults to 'salesforce').",
    )
    parser.add_argument(
        "--salesforce",
        default=str(repo_path("RelatedMaterials", "Sample", "Salesforce Spreadsheet Input - DummyData.xlsx")),
        help="Path to the local .xlsx export. Only used when the Salesforce data source resolves to 'csv' (the local fallback); ignored when pulling live from Salesforce.",
    )
    parser.add_argument("--tableau", default=str(repo_path("RelatedMaterials", "Sample", "Tableau - DummyData.csv")))
    parser.add_argument("--scorecard", default=str(repo_path("RelatedMaterials", "Sample", "Consultant Scorecard 2026-03 - DummyData.xlsx")))
    parser.add_argument("--append", action="store_true", help="Append rows instead of replacing existing rows in each table.")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.source:
        os.environ["DATA_SOURCE"] = args.source

    database = get_database()
    database.ensure_schema_ready()
    counts = ingest_all_sources(
        database=database,
        salesforce_path=args.salesforce,
        tableau_path=args.tableau,
        scorecard_path=args.scorecard,
        replace_existing=not args.append,
    )

    for table_name, count in counts.items():
        print(f"{table_name}: {count} rows ingested")


if __name__ == "__main__":
    main()
