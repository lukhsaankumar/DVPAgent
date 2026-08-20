from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the DVP Meeting Prep web app (UI + API).")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--advisor-source",
        choices=["legacy", "auto"],
        default=None,
        help="Which Salesforce table the advisor search dropdown and meeting-prep generation read from: "
        "'legacy' (the manually-provided spreadsheet) or 'auto' (a live Salesforce extraction). Advisors whose "
        "only data is in Tableau/the consultant scorecard still show up either way. Overrides "
        "ADVISOR_SOURCE_MODE in .env for this run only; defaults to whatever's already configured there "
        "(itself defaulting to 'legacy') if not passed.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.advisor_source:
        os.environ["ADVISOR_SOURCE_MODE"] = args.advisor_source

    # Imported after the env override above, and after sys.path is set up --
    # get_settings() (called from the app's lifespan startup, not at import
    # time) needs ADVISOR_SOURCE_MODE to already be in the environment.
    import uvicorn

    from dvp_meeting_prep.webapp.app import app

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
