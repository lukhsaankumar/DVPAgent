from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dvp_meeting_prep.config import get_settings
from dvp_meeting_prep.db import SALESFORCE_TABLE_BY_ADVISOR_SOURCE_MODE, get_database
from dvp_meeting_prep.files import pretty_json
from dvp_meeting_prep.llm import close_gemini_client, generate_meeting_prep
from dvp_meeting_prep.prompting import build_meeting_prep_prompt
from dvp_meeting_prep.query import fetch_all_sources_for_advisor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Query advisor data from SQLite and generate a meeting prep document.")
    parser.add_argument("advisor_name", nargs="?", help="Exact advisor name as stored in the source tables.")
    parser.add_argument("--save", help="Optional file path to save the generated meeting prep document.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    advisor_name = args.advisor_name or input("Advisor name: ").strip()
    if not advisor_name:
        raise RuntimeError("Advisor name is required.")

    database = get_database()
    salesforce_table = SALESFORCE_TABLE_BY_ADVISOR_SOURCE_MODE[get_settings().advisor_source_mode]
    source_results = fetch_all_sources_for_advisor(database, advisor_name, salesforce_table=salesforce_table)

    for table_name, rows in source_results.items():
        print(f"\n== {table_name} ==")
        print(pretty_json(rows))

    prompt = build_meeting_prep_prompt(advisor_name, source_results)
    print("\n== Prompt ==")
    print(prompt)

    try:
        response = generate_meeting_prep(prompt)
    finally:
        close_gemini_client()
    print("\n== Meeting Prep ==")
    print(response)

    if args.save:
        Path(args.save).write_text(response, encoding="utf-8")
        print(f"\nSaved meeting prep to {args.save}")


if __name__ == "__main__":
    main()
