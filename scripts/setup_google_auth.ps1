<#
.SYNOPSIS
    Idempotent local Google Application Default Credentials (ADC) setup:
    verifies the gcloud CLI is installed, runs the interactive ADC login if
    not already done, optionally sets gcloud's default project, then runs
    scripts/check_google_auth.py to verify the app's own configuration
    resolves correctly.

.DESCRIPTION
    This never makes a paid Gemini call and never downloads an unverified
    executable -- if gcloud isn't installed, it points you at the official
    installer instead of fetching one itself. `gcloud auth application-default
    login` is the one interactive step (opens a browser) and is skipped if
    ADC credentials already exist on disk.

.EXAMPLE
    .\scripts\setup_google_auth.ps1

.EXAMPLE
    .\scripts\setup_google_auth.ps1 -Project my-gcp-project-id
#>
[CmdletBinding()]
param(
    [string]$Project
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host "[SETUP] $Message"
}

# 1. Verify the gcloud CLI is on PATH. This is the one piece the app itself
# never needs at runtime (application code never shells out to gcloud) --
# it's only used here, once, for the interactive login.
$gcloudCmd = Get-Command gcloud -ErrorAction SilentlyContinue
if (-not $gcloudCmd) {
    Write-Error "[SETUP] gcloud was not found on PATH. Install the Google Cloud CLI from the official installer: https://cloud.google.com/sdk/docs/install -- then re-run this script."
    exit 1
}
Write-Step "gcloud found: $($gcloudCmd.Source)"

# 2. Check whether ADC already exists on disk (a plain file check -- makes
# no network call and never prints the file's contents).
$adcPath = Join-Path $env:APPDATA "gcloud\application_default_credentials.json"
if (Test-Path $adcPath) {
    Write-Step "Application Default Credentials already present at $adcPath -- skipping login."
} else {
    Write-Step "No ADC file found at $adcPath -- running the interactive login (opens a browser)."
    gcloud auth application-default login
    if ($LASTEXITCODE -ne 0) {
        Write-Error "[SETUP] 'gcloud auth application-default login' failed (exit code $LASTEXITCODE)."
        exit $LASTEXITCODE
    }
}

# 3. Optionally point gcloud's own default project at the target project
# (convenience only -- this app reads GOOGLE_CLOUD_PROJECT from .env, not
# from gcloud's config, so this step is not required for the app to work).
if ($Project) {
    Write-Step "Setting gcloud's default project to '$Project'"
    gcloud config set project $Project
}

# 4. Verify the app's own configuration resolves ADC + project/location/model
# correctly, without making a paid call.
Write-Step "Running scripts/check_google_auth.py"
python scripts/check_google_auth.py
if ($LASTEXITCODE -ne 0) {
    Write-Warning "[SETUP] scripts/check_google_auth.py reported a problem (exit code $LASTEXITCODE) -- see its output above. This is often just a missing GOOGLE_CLOUD_PROJECT/LOCATION/MODEL in .env, not an ADC problem."
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "[SETUP] Google ADC is ready. Start the app with:"
Write-Host "  python scripts/run_server.py"
