from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import List

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dvp_meeting_prep.db import Database, fetch_all, get_database
from dvp_meeting_prep.files import pretty_json, slugify_filename
from dvp_meeting_prep.query import fetch_all_sources_for_advisor
from dvp_meeting_prep.prompting import build_meeting_prep_prompt
from dvp_meeting_prep.llm import close_gemini_client, generate_meeting_prep


def sample_advisors(database: Database, limit: int = 5) -> List[str]:
    # try salesforce first, then tableau, then the scorecard mirror
    for table in ("salesforce_data", "tableau_data", "consultant_scorecard_data"):
        try:
            with database.read() as conn:
                rows = fetch_all(conn, f"SELECT DISTINCT advisor_name FROM {table} LIMIT ?", (limit,))
            names = []
            for r in rows:
                name = r.get("advisor_name")
                if name and name not in names:
                    names.append(name)
            if names:
                return names
        except Exception:
            continue
    # fallback examples
    return ["Avery Benton", "YORKE, ELLIS", "Example Advisor"]


def choose_name(examples: List[str]) -> str:
    print("Select an advisor from the examples or type a full advisor name:")
    for i, ex in enumerate(examples, start=1):
        print(f"  {i}. {ex}")
    choice = input("Enter number or name: ").strip()
    if not choice:
        raise SystemExit("No advisor chosen. Exiting.")
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(examples):
            return examples[idx]
    return choice


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Interactive advisor meeting-prep helper.")
    parser.add_argument(
        "--query-output",
        default="query_output.txt",
        help="Path to save fetched query results as formatted JSON text.",
    )
    parser.add_argument(
        "--prompt-output",
        default="prompt_output.txt",
        help="Path to save the full generated prompt text.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    database = get_database()
    examples = sample_advisors(database, limit=5)
    advisor_name = choose_name(examples)

    print(f"\nFetching rows for advisor: {advisor_name}\n")
    source_results = fetch_all_sources_for_advisor(database, advisor_name)

    for table_name, rows in source_results.items():
        print(f"\n== {table_name} ({len(rows)} rows) ==")
        print(pretty_json(rows[:5]))

    output_path = Path(args.query_output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_payload = {
        "advisor_name": advisor_name,
        "source_results": source_results,
    }
    output_path.write_text(pretty_json(output_payload), encoding="utf-8")
    print(f"\nSaved full query output to: {output_path}")

    prompt = build_meeting_prep_prompt(advisor_name, source_results)
    prompt_output_path = Path(args.prompt_output)
    prompt_output_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_output_path.write_text(prompt, encoding="utf-8")

    print("\n== Prompt Preview ==")
    print(prompt[:2000])
    print(f"\nSaved full prompt to: {prompt_output_path}")

    do_generate = input("Call Gemini to generate meeting prep now? [y/N]: ").strip().lower() == "y"
    if not do_generate:
        print("Skipping LLM call. You can run scripts/run_meeting_prep.py later.")
        return

    try:
        content = generate_meeting_prep(prompt)
    except Exception as e:
        # Never print the prompt on failure -- it contains Salesforce
        # comments/notes and other advisor-sensitive source data.
        print("LLM call failed:", e)
        return
    finally:
        close_gemini_client()

    default_output_path = Path("output") / f"{slugify_filename(advisor_name)}_meeting_prep.md"
    default_output_path.parent.mkdir(parents=True, exist_ok=True)
    default_output_path.write_text(content, encoding="utf-8")

    print("\n== Meeting Prep ==\n")
    print(content)
    print(f"\nSaved Markdown meeting prep to: {default_output_path}")

    save = input("Save to file? (path or leave empty): ").strip()
    if save:
        Path(save).write_text(content, encoding="utf-8")
        print(f"Saved to {save}")


if __name__ == "__main__":
    main()
