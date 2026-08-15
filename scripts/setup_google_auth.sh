#!/usr/bin/env bash
# Idempotent local Google Application Default Credentials (ADC) setup for
# WSL/Linux/macOS: verifies the gcloud CLI is installed, runs the interactive
# ADC login if not already done, optionally sets gcloud's default project,
# then runs scripts/check_google_auth.py to verify the app's own
# configuration resolves correctly.
#
# This never makes a paid Gemini call and never downloads an unverified
# executable -- if gcloud isn't installed, it points you at the official
# installer instead of fetching one itself. `gcloud auth
# application-default login` is the one interactive step (opens a browser)
# and is skipped if ADC credentials already exist on disk.
#
# Usage:
#   bash scripts/setup_google_auth.sh
#   bash scripts/setup_google_auth.sh --project my-gcp-project-id
set -euo pipefail

PROJECT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --project)
      PROJECT="${2:-}"
      shift 2
      ;;
    *)
      echo "[SETUP] Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

step() { echo "[SETUP] $1"; }

# 1. Verify the gcloud CLI is on PATH. This is the one piece the app itself
# never needs at runtime (application code never shells out to gcloud) --
# it's only used here, once, for the interactive login.
if ! command -v gcloud >/dev/null 2>&1; then
  echo "[SETUP] gcloud was not found on PATH. Install the Google Cloud CLI from the official installer: https://cloud.google.com/sdk/docs/install -- then re-run this script." >&2
  exit 1
fi
step "gcloud found: $(command -v gcloud)"

# 2. Check whether ADC already exists on disk (a plain file check -- makes
# no network call and never prints the file's contents).
ADC_PATH="$HOME/.config/gcloud/application_default_credentials.json"
if [ -f "$ADC_PATH" ]; then
  step "Application Default Credentials already present at $ADC_PATH -- skipping login."
else
  step "No ADC file found at $ADC_PATH -- running the interactive login (opens a browser)."
  gcloud auth application-default login
fi

# 3. Optionally point gcloud's own default project at the target project
# (convenience only -- this app reads GOOGLE_CLOUD_PROJECT from .env, not
# from gcloud's config, so this step is not required for the app to work).
if [ -n "$PROJECT" ]; then
  step "Setting gcloud's default project to '$PROJECT'"
  gcloud config set project "$PROJECT"
fi

# 4. Verify the app's own configuration resolves ADC + project/location/model
# correctly, without making a paid call.
step "Running scripts/check_google_auth.py"
python scripts/check_google_auth.py

echo
echo "[SETUP] Google ADC is ready. Start the app with:"
echo "  python scripts/run_server.py"
