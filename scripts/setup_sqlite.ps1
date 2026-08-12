<#
.SYNOPSIS
    Idempotent local SQLite setup: verifies Python's built-in sqlite3 support,
    optionally installs the (purely optional) sqlite3 CLI, then runs
    scripts/init_sqlite.py and scripts/check_sqlite_tables.py.

.DESCRIPTION
    The application itself never needs the sqlite3 command-line program --
    Python's bundled sqlite3 module is sufficient. -InstallCli only affects
    whether a CLI is installed for your own manual inspection.

.EXAMPLE
    .\scripts\setup_sqlite.ps1

.EXAMPLE
    .\scripts\setup_sqlite.ps1 -InstallCli
#>
[CmdletBinding()]
param(
    [switch]$InstallCli
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host "[SETUP] $Message"
}

# 1. Verify Python exists.
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Error "[SETUP] Python was not found on PATH. Install Python 3.12+ and try again."
    exit 1
}
Write-Step "Python found: $($pythonCmd.Source)"

# 2. Verify Python's built-in SQLite support.
$sqliteVersion = & python -c "import sqlite3; print(sqlite3.sqlite_version)" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error "[SETUP] Python's built-in sqlite3 module is not available: $sqliteVersion"
    exit 1
}
Write-Step "Python sqlite3 module OK (SQLite $sqliteVersion)"

# 3. Check whether the (optional) sqlite3 CLI is available.
$cliCmd = Get-Command sqlite3 -ErrorAction SilentlyContinue
if ($cliCmd) {
    Write-Step "sqlite3 CLI found: $($cliCmd.Source) (optional -- only used for manual inspection)"
} else {
    Write-Step "sqlite3 CLI not found (optional -- the app only needs Python's built-in sqlite3 module)"

    # 4. Only attempt installation if explicitly requested.
    if ($InstallCli) {
        $wingetCmd = Get-Command winget -ErrorAction SilentlyContinue
        if (-not $wingetCmd) {
            Write-Warning "[SETUP] winget was not found. To install the CLI manually, download it from https://www.sqlite.org/download.html (official site) -- not required to run the app."
        } else {
            Write-Step "Attempting to install the SQLite CLI via winget (SQLite.SQLite)..."
            try {
                winget install --id SQLite.SQLite -e --source winget --accept-source-agreements --accept-package-agreements
            } catch {
                Write-Warning "[SETUP] winget install failed: $_"
            }
            # 5. Recheck availability -- never fall back to downloading an
            # unverified executable from an arbitrary URL.
            $cliCmd = Get-Command sqlite3 -ErrorAction SilentlyContinue
            if ($cliCmd) {
                Write-Step "sqlite3 CLI now available: $($cliCmd.Source)"
            } else {
                Write-Warning "[SETUP] sqlite3 CLI still not found after the install attempt. This is optional -- if you want it, install manually from https://www.sqlite.org/download.html."
            }
        }
    }
}

# 6-7. scripts/init_sqlite.py creates the data directory itself and applies
# the schema/migrations (idempotent, non-destructive).
Write-Step "Running scripts/init_sqlite.py"
python scripts/init_sqlite.py
if ($LASTEXITCODE -ne 0) {
    Write-Error "[SETUP] scripts/init_sqlite.py failed (exit code $LASTEXITCODE)."
    exit $LASTEXITCODE
}

# 8. Verify the result.
Write-Step "Running scripts/check_sqlite_tables.py"
python scripts/check_sqlite_tables.py
if ($LASTEXITCODE -ne 0) {
    Write-Error "[SETUP] scripts/check_sqlite_tables.py failed (exit code $LASTEXITCODE)."
    exit $LASTEXITCODE
}

# 9. Tell the user what to run next.
Write-Host ""
Write-Host "[SETUP] SQLite is ready. Start the app with:"
Write-Host "  python scripts/run_server.py"
