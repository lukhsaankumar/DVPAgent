#!/usr/bin/env bash
# Idempotent local SQLite setup for WSL/Linux: verifies Python's built-in
# sqlite3 support, optionally installs the (purely optional) sqlite3 CLI,
# then runs scripts/init_sqlite.py and scripts/check_sqlite_tables.py.
#
# The application itself never needs the sqlite3 command-line program --
# Python's bundled sqlite3 module is sufficient. --install-cli only affects
# whether a CLI is installed for your own manual inspection.
#
# Usage:
#   bash scripts/setup_sqlite.sh
#   bash scripts/setup_sqlite.sh --install-cli
set -euo pipefail

INSTALL_CLI=false
for arg in "$@"; do
  case "$arg" in
    --install-cli) INSTALL_CLI=true ;;
    *)
      echo "[SETUP] Unknown argument: $arg" >&2
      exit 1
      ;;
  esac
done

step() { echo "[SETUP] $1"; }

# 1. Verify Python exists. Check that the candidate actually runs, not just
# that it's on PATH -- on Windows, `python3`/`python` can resolve to a
# non-functional "App Execution Alias" stub when no real interpreter is
# registered under that exact name, even though a working one exists
# elsewhere (e.g. python.exe inside a venv earlier on PATH).
PYTHON=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "print(1)" >/dev/null 2>&1; then
    PYTHON="$candidate"
    break
  fi
done
if [ -z "$PYTHON" ]; then
  echo "[SETUP] Python was not found on PATH (or resolved to a non-functional stub). Install Python 3.12+ and try again." >&2
  exit 1
fi
step "Python found: $(command -v "$PYTHON")"

# 2. Verify Python's built-in SQLite support.
SQLITE_VERSION=$("$PYTHON" -c "import sqlite3; print(sqlite3.sqlite_version)")
step "Python sqlite3 module OK (SQLite $SQLITE_VERSION)"

# 3. Check whether the (optional) sqlite3 CLI is available.
if command -v sqlite3 >/dev/null 2>&1; then
  step "sqlite3 CLI found: $(command -v sqlite3) (optional -- only used for manual inspection)"
else
  step "sqlite3 CLI not found (optional -- the app only needs Python's built-in sqlite3 module)"

  # 4. Only attempt installation if explicitly requested, and only invoke
  # sudo in that case.
  if [ "$INSTALL_CLI" = true ]; then
    if command -v apt-get >/dev/null 2>&1; then
      step "Installing sqlite3 CLI via apt-get (this will prompt for sudo)..."
      sudo apt-get update
      sudo apt-get install -y sqlite3
      if command -v sqlite3 >/dev/null 2>&1; then
        step "sqlite3 CLI now available: $(command -v sqlite3)"
      else
        echo "[SETUP] sqlite3 CLI still not found after apt-get install. This is optional -- the application does not need it." >&2
      fi
    else
      echo "[SETUP] apt-get not found -- install the sqlite3 CLI manually for your distribution if you want it (not required to run the app)." >&2
    fi
  fi
fi

# 5-6. scripts/init_sqlite.py creates the data directory itself and applies
# the schema/migrations (idempotent, non-destructive).
step "Running scripts/init_sqlite.py"
"$PYTHON" scripts/init_sqlite.py

# 7. Verify the result.
step "Running scripts/check_sqlite_tables.py"
"$PYTHON" scripts/check_sqlite_tables.py

echo
echo "[SETUP] SQLite is ready. Start the app with:"
echo "  $PYTHON scripts/run_server.py"
