from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import google.auth
from google.auth.exceptions import DefaultCredentialsError, RefreshError
from google.auth.transport.requests import Request as GoogleAuthRequest

from dvp_meeting_prep.config import configure_logging, get_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check Google Application Default Credentials and Gemini configuration without making a paid call by default."
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="After the auth check, also make one minimal real Gemini generation call to confirm end-to-end connectivity. Costs money/quota.",
    )
    parser.add_argument(
        "--yes-i-want-to-call-gemini",
        action="store_true",
        help="Required alongside --live to actually place the live call (explicit confirmation so --live can't fire by accident).",
    )
    return parser


def _live_confirmed(args: argparse.Namespace) -> bool:
    if args.yes_i_want_to_call_gemini:
        return True
    return os.environ.get("RUN_GEMINI_INTEGRATION_TESTS", "").strip().lower() in {"1", "true", "yes"}


def main() -> int:
    configure_logging()
    args = build_parser().parse_args()

    print("[CONFIG] Loading application configuration")
    try:
        settings = get_settings()
    except Exception as exc:
        print(f"[CONFIG] FAILED to load configuration: {exc}")
        return 2

    gemini = settings.gemini
    print(f"[CONFIG] Configured project: {gemini.project}")
    print(f"[CONFIG] Configured location: {gemini.location}")
    print(f"[CONFIG] Configured model: {gemini.model}")
    print(f"[CONFIG] API version: {gemini.api_version}")

    print("\n[AUTH] Discovering Application Default Credentials")
    try:
        credentials, adc_project = google.auth.default()
    except DefaultCredentialsError as exc:
        print("[AUTH] ADC found: false")
        print(f"[AUTH] ERROR: {exc}")
        print("\nRun: gcloud auth application-default login")
        return 3

    print("[AUTH] ADC found: true")
    print(f"[AUTH] Credential class: {type(credentials).__name__}")
    print(f"[AUTH] Resolved ADC project: {adc_project or '(none reported by ADC)'}")

    print("\n[AUTH] Refreshing credentials (the token itself is never printed)")
    try:
        credentials.refresh(GoogleAuthRequest())
    except RefreshError as exc:
        print(f"[AUTH] ERROR: could not refresh credentials: {exc}")
        return 4
    print(f"[AUTH] Credentials valid after refresh: {credentials.valid}")

    if adc_project and adc_project != gemini.project:
        print(
            f"\n[WARNING] ADC-resolved project ({adc_project!r}) differs from GOOGLE_CLOUD_PROJECT "
            f"({gemini.project!r}). Gemini calls use GOOGLE_CLOUD_PROJECT explicitly, not the ADC "
            "project, so this is only a problem if you expected them to match."
        )

    if not args.live:
        print(
            "\n[COMPLETE] Authentication check passed. Pass --live "
            "(with --yes-i-want-to-call-gemini, or RUN_GEMINI_INTEGRATION_TESTS=true) "
            "to also run a minimal real generation call."
        )
        return 0

    if not _live_confirmed(args):
        print(
            "\n[LIVE] --live was passed but not confirmed. Re-run with "
            "--yes-i-want-to-call-gemini, or set RUN_GEMINI_INTEGRATION_TESTS=true, "
            "to actually place a real (billable) Gemini call."
        )
        return 5

    print("\n[LIVE] Making a minimal real Gemini generation call...")
    from dvp_meeting_prep.llm import close_gemini_client, generate_meeting_prep

    try:
        result = generate_meeting_prep("Reply with exactly one word: OK")
    except Exception as exc:
        print(f"[LIVE] FAILED: {type(exc).__name__}: {exc}")
        return 6
    finally:
        close_gemini_client()

    print(f"[LIVE] SUCCESS: received {len(result)} character(s) of response text.")
    print("[COMPLETE] Live Gemini smoke test passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
