from __future__ import annotations

from pathlib import Path
import sys
from typing import List

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dvp_meeting_prep.db import get_supabase_client
from dvp_meeting_prep.files import pretty_json
from dvp_meeting_prep.query import fetch_all_sources_for_advisor
from dvp_meeting_prep.prompting import build_meeting_prep_prompt
from dvp_meeting_prep.llm import generate_meeting_prep
from dvp_meeting_prep.config import get_settings


def sample_advisors(client, limit: int = 5) -> List[str]:
    # try salesforce first, then tableau
    for table in ("salesforce_data", "tableau_data", "consultant_scorecard_data"):
        try:
            resp = client.table(table).select("advisor_name").limit(limit).execute()
            rows = resp.data or []
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


def main() -> None:
    client = get_supabase_client()
    examples = sample_advisors(client, limit=5)
    advisor_name = choose_name(examples)

    print(f"\nFetching rows for advisor: {advisor_name}\n")
    source_results = fetch_all_sources_for_advisor(client, advisor_name)

    for table_name, rows in source_results.items():
        print(f"\n== {table_name} ({len(rows)} rows) ==")
        print(pretty_json(rows[:5]))

    prompt = build_meeting_prep_prompt(advisor_name, source_results)
    print("\n== Prompt Preview ==")
    print(prompt[:2000])

    do_generate = input("Call OpenAI to generate meeting prep now? [y/N]: ").strip().lower() == "y"
    if not do_generate:
        print("Skipping LLM call. You can run scripts/run_meeting_prep.py later.")
        return

    try:
        content = generate_meeting_prep(prompt)
    except Exception as e:
        print("LLM call failed:", e)
        print("Prompt was:\n", prompt)
        return

    print("\n== Meeting Prep ==\n")
    print(content)

    save = input("Save to file? (path or leave empty): ").strip()
    if save:
        Path(save).write_text(content, encoding="utf-8")
        print(f"Saved to {save}")


if __name__ == "__main__":
    main()
