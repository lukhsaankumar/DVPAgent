from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dvp_meeting_prep.db import get_database
from dvp_meeting_prep.files import pretty_json
from dvp_meeting_prep.query import fetch_all_sources_for_advisor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Query all source tables for an exact advisor name.")
    parser.add_argument("advisor_name", help="Exact advisor name as stored in the source tables.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    database = get_database()
    results = fetch_all_sources_for_advisor(database, args.advisor_name)

    for table_name, rows in results.items():
        print(f"\n== {table_name} ==")
        print(pretty_json(rows))


if __name__ == "__main__":
    main()

