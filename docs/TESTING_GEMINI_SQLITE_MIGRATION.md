# Testing: Gemini Enterprise + SQLite migration

This document is a testing record for the migration of this app from
**OpenAI + Supabase/Postgres** to **Gemini Enterprise (Google ADC) +
local SQLite**. Every result below is either an actual command run during
this migration (with its actual output) or an instruction for a step that
requires access this environment doesn't have (a real Salesforce org, a
Google Cloud project with billing/quota, or an interactive browser) --
those are explicitly labeled as such. Nothing here is a claim about a test
that wasn't actually executed.

## 1) Overview

Two independent replacements, done in two phases so the app stayed runnable
after each:

- **LLM provider**: OpenAI API key -> Gemini Enterprise via the
  `google-genai` SDK (`genai.Client(enterprise=True, ...)`) authenticated
  with Google Application Default Credentials. No API key exists anywhere
  in the app. See `src/dvp_meeting_prep/llm.py`.
- **Database**: Supabase (hosted Postgres, REST client) -> a single local
  SQLite file via Python's built-in `sqlite3` module. No server, account,
  or ORM. See `src/dvp_meeting_prep/db.py` and `sql/schema.sql`.

Everything else -- Salesforce extraction, CSV fallback, Tableau/scorecard
parsing and dedup logic, advisor search/name-bridging, prompt building,
Markdown->DOCX conversion, the FastAPI routes and their request/response
shapes -- is unchanged in behavior; only the storage and LLM layers under
those features were replaced.

## 2) Prerequisites

- Python 3.12+ (developed/tested against 3.12.6).
- A Google Cloud project with the Vertex AI API enabled and Gemini
  Enterprise access, plus the [gcloud CLI](https://cloud.google.com/sdk/docs/install)
  installed locally for the one-time `gcloud auth application-default
  login` step. The application itself never shells out to `gcloud`.
- No database server, account, or API key of any kind.
- (Optional) A Salesforce sandbox/production org, only if you intend to use
  `DATA_SOURCE=salesforce` instead of the `.xlsx`/`DATA_SOURCE=csv`
  fallback -- see [SALESFORCE_SETUP.md](SALESFORCE_SETUP.md).

Installed dependency versions actually used for the test run recorded in
this document (from `python -m pip list` in the project's `.venv`):

| Package | Version |
| --- | --- |
| Python | 3.12.6 |
| sqlite3 (stdlib, linked SQLite library) | 3.45.3 |
| google-genai | 2.17.0 |
| google-auth | 2.56.3 |
| fastapi | 0.141.1 |
| uvicorn | 0.52.1 |
| python-docx | 1.2.0 |
| mistune | 3.3.4 |
| openpyxl | 3.1.5 |
| simple-salesforce | 1.12.10 |
| pytest | 9.1.1 |
| pyflakes | 3.4.0 |

## 3) Environment configuration

New/changed environment variables (see `.env.example`,
`.env.sandbox.example`, `.env.production.example`):

| Variable | Default | Purpose |
| --- | --- | --- |
| `LLM_PROVIDER` | `gemini_enterprise` | Only supported value; kept as a config field for symmetry with `DATABASE_BACKEND`. |
| `GOOGLE_CLOUD_PROJECT` | *(required, no default)* | GCP project ID Gemini Enterprise calls are billed/scoped to. |
| `GOOGLE_CLOUD_LOCATION` | *(required, no default)* | e.g. `us-central1`. |
| `GOOGLE_GENAI_MODEL` | *(required, no default)* | e.g. `gemini-2.0-flash-001`; must be a model your project has access to. |
| `GOOGLE_GENAI_API_VERSION` | `v1` | Passed to `types.HttpOptions(api_version=...)`. |
| `GOOGLE_GENAI_TEMPERATURE` | `0.2` | |
| `GOOGLE_GENAI_MAX_OUTPUT_TOKENS` | `8192` | |
| `GOOGLE_GENAI_REQUEST_TIMEOUT_SECONDS` | `120` | |
| `GOOGLE_GENAI_MAX_RETRIES` | `3` | Bounded retries on 429/500/502/503/504/connection-reset/timeout only. |
| `STORE_LLM_AUDIT_CONTENT` | `true` | Whether prompt/response text is written to `meeting_prep_documents`. |
| `DATABASE_BACKEND` | `sqlite` | Only supported value. |
| `SQLITE_DB_PATH` | `data/dvp_meeting_prep.sqlite3` | Resolved relative to the project root, not CWD, when not absolute. |
| `SQLITE_BUSY_TIMEOUT_MS` | `10000` | |
| `SQLITE_JOURNAL_MODE` | `WAL` | |
| `SQLITE_FOREIGN_KEYS` | `true` | |
| `SQLITE_SYNCHRONOUS` | `NORMAL` | |
| `SQLITE_DEBUG` | `false` | When true, every SQL statement is logged via `sqlite3.Connection.set_trace_callback`. |

Removed entirely (no longer read anywhere, and no fallback to them):
`OPENAI_API_KEY`, `OPENAI_MODEL`, `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`,
`SUPABASE_SECRET_KEY`, `SUPABASE_URI`.

All `SF_*` Salesforce variables and `DATA_SOURCE`/`APP_ENV`/`ENV_FILE`/
`CSV_INPUT_PATH` are unchanged.

## 4) Google ADC setup (exact commands)

```powershell
gcloud auth application-default login
```

Opens a browser, signs in, writes credentials to
`%APPDATA%\gcloud\application_default_credentials.json` (Windows) or
`~/.config/gcloud/application_default_credentials.json` (macOS/Linux/WSL).
`google.auth.default()` discovers this automatically; nothing in this app
references that path directly, and the app never runs `gcloud` itself.

```powershell
python scripts/check_google_auth.py
```

Verifies ADC is discoverable and prints the configured
project/location/model/api_version **without making a paid call**. Actual
output from this environment, which genuinely has no `gcloud` installed and
no ADC configured (confirming the failure path works and no live call was
attempted):

```
[CONFIG] Loading application configuration
[CONFIG] Configured project: test-project
[CONFIG] Configured location: us-central1
[CONFIG] Configured model: gemini-test-model
[CONFIG] API version: v1

[AUTH] Discovering Application Default Credentials
[AUTH] ADC found: false
[AUTH] ERROR: Your default credentials were not found. To set up Application
Default Credentials, see https://cloud.google.com/docs/authentication/external/set-up-adc
for more information.

Run: gcloud auth application-default login
```
(exit code 2)

To additionally confirm live connectivity (real project, real ADC, costs
quota):

```powershell
python scripts/check_google_auth.py --live --yes-i-want-to-call-gemini
```

**Not run in this environment** -- no Google Cloud project or ADC is
available here. This is the one live-call path in the whole script, and it
requires the explicit double-opt-in flag (or `RUN_GEMINI_INTEGRATION_TESTS=true`)
specifically so it never fires by accident.

## 5) SQLite setup

### PowerShell (Windows)

```powershell
.\scripts\setup_sqlite.ps1                # verify Python + sqlite3 module, run init + check
.\scripts\setup_sqlite.ps1 -InstallCli    # also installs the sqlite3 CLI via winget (manual inspection only)
```

### WSL / Linux / macOS

```bash
bash scripts/setup_sqlite.sh
bash scripts/setup_sqlite.sh --install-cli   # via apt-get; never invokes sudo unless this flag is passed
```

### Docker

`docker compose up --build` runs `ensure_schema_ready()` automatically as
part of the FastAPI lifespan startup handler (before the app accepts
requests) -- no separate init step is needed inside the container. See
section 11 ("Docker persistence") below for the actual verification that was
run.

### Actual init + verify output (this environment)

```powershell
python scripts/init_sqlite.py
```
```
[MIGRATION] Applying 0001_initial
[MIGRATION] 0001_initial complete
[CONFIG] SQLite path: <project_root>\data\dvp_meeting_prep.sqlite3
[CONFIG] Parent directory ready: <project_root>\data
[DATABASE] Opening SQLite database
[DATABASE] SQLite version: 3.45.3 (0.00s)
[SCHEMA] Checking schema migrations
[SCHEMA] Migration check complete (0.03s)
[SCHEMA] Verifying expected tables
[SCHEMA] Found 8 application tables
[SCHEMA] Found 14 indexes
[COMPLETE] SQLite database is ready (0.03s total)
```

```powershell
python scripts/check_sqlite_tables.py
```
```
[DATABASE] Path: <project_root>\data\dvp_meeting_prep.sqlite3
[DATABASE] File size: 110592 bytes
[DATABASE] SQLite version: 3.45.3

[INTEGRITY] Running PRAGMA integrity_check
[INTEGRITY] OK

[FOREIGN KEYS] Checking PRAGMA foreign_keys
[FOREIGN KEYS] Enabled: true
[FOREIGN KEYS] Running PRAGMA foreign_key_check
[FOREIGN KEYS] No violations

[MIGRATIONS] Applied migrations:
  0001  initial  2026-08-12T05:19:49.993Z

[SCHEMA] Verifying expected tables
[SCHEMA] All 8 expected tables present

[SCHEMA] Verifying expected indexes
[SCHEMA] All 14 expected indexes present

[ROW COUNTS]
  salesforce_data: 0
  tableau_data: 0
  consultant_scorecard_data: 0
  consultant_scorecard_raw: 0
  consultant_scorecard_monthly: 0
  consultant_scorecard_metric: 0
  upload_batches: 0
  meeting_prep_documents: 0

[COMPLETE] All checks passed
```

## 6) Automated unit tests -- actual results

```powershell
python -m pytest tests/
```

**Actual result: 166 passed, 0 failed, in 5.20s** (`tests/e2e_smoke_test.py`
is excluded from this run -- it is a manual Playwright script, not a pytest
module; see section 9). Full breakdown by file, all PASSED:

| Test file | Tests | Covers |
| --- | --- | --- |
| `test_data_source.py` | 6 | DATA_SOURCE factory (csv vs salesforce) |
| `test_end_to_end_mocked.py` | 1 | Full pipeline: SQLite ingest -> query -> prompt -> mocked Gemini -> Markdown -> DOCX |
| `test_gemini_config.py` | 20 | Every Gemini env var: missing/invalid/default values |
| `test_gemini_llm.py` | 25 | Client construction, retries, error classification, logging privacy, lifecycle |
| `test_salesforce_client.py` | 12 | Salesforce auth (unchanged by this migration) |
| `test_salesforce_config.py` | 17 | Settings loading, including the new required Gemini vars |
| `test_salesforce_metadata.py` | 10 | Field discovery (unchanged) |
| `test_salesforce_normalize.py` | 6 | Row normalization (unchanged) |
| `test_salesforce_queries.py` | 18 | SOQL building (unchanged) |
| `test_salesforce_validate.py` | 7 | Count/integrity validation (unchanged) |
| `test_sqlite_db.py` | 17 | Schema init, migrations, transactions, locking, FKs, injection safety, health checks |
| `test_sqlite_ingest.py` | 7 | Atomic replace, tableau/scorecard dedup, upsert, JSON/bool round-trip |
| `test_sqlite_query_and_advisors.py` | 8 | Advisor search, cross-source name/number bridging, audit tables |
| **Total** | **166** | |

Every test that touches a database uses a throwaway file under pytest's
`tmp_path` (the `sqlite_db` fixture in `tests/conftest.py`) -- none of them
touch `data/dvp_meeting_prep.sqlite3`. Every test that touches Gemini mocks
`google.genai.Client` (see `tests/test_gemini_llm.py`'s `FakeGenaiClient`) --
none of them call a real Google API, use ADC, or require a Google Cloud
project. `simple-salesforce` is fully mocked in the Salesforce test files, as
it was before this migration. **No live external service was contacted by
the automated test suite.**

## 7) Static analysis -- actual results

```powershell
python -m pyflakes src/dvp_meeting_prep scripts tests
```

**Actual result: exit code 0, no output** (clean -- no unused imports,
unused variables, or undefined names anywhere in the application, scripts,
or test code).

```powershell
python -m py_compile src/dvp_meeting_prep/*.py src/dvp_meeting_prep/**/*.py scripts/*.py tests/*.py
```

**Actual result: exit code 0** (every module compiles).

## 8) Mocked end-to-end test walkthrough

`tests/test_end_to_end_mocked.py::test_full_pipeline_sqlite_to_docx_with_mocked_gemini`
(actually run as part of the 166 passing tests above) exercises the real
production code path end to end, with only the Gemini network call
replaced by a fake:

1. Ingests real Salesforce-shaped and Tableau-shaped rows into a real
   temp-file SQLite database via `ingest_rows()`.
2. Calls the real `fetch_all_sources_for_advisor()` against that database.
3. Calls the real `build_meeting_prep_prompt()` to build the actual prompt
   text that would be sent to Gemini.
4. Monkeypatches `google.genai.Client` with a fake that returns a canned
   Markdown response, then calls the real `generate_meeting_prep()` --
   asserting the *exact* prompt text was what got sent to the (fake)
   client, and that its retry/response-parsing logic runs unmodified.
5. Writes the resulting prompt/response into `meeting_prep_documents` via a
   real transaction and reads it back.
6. Calls the real `markdown_to_docx_bytes()` and opens the resulting bytes
   with `python-docx` to assert the generated `.docx` actually contains the
   expected text.

This is the same code path the FastAPI `/api/meeting-prep` endpoint uses; a
real HTTP-level walkthrough (with a genuinely running server and a real
upload) is also documented and was actually run -- see section 10 below.

## 9) Salesforce smoke test (requires real Salesforce access -- not run here)

`scripts/salesforce_extract.py --dry-run` (and the full
`tests/e2e_smoke_test.py` Playwright script) connect to a real Salesforce
org and are **not run automatically**; they require real sandbox/production
credentials this environment does not have. To run them yourself:

```powershell
python scripts/salesforce_extract.py --discover-salesforce
python scripts/salesforce_extract.py --dry-run
```

See [SALESFORCE_SETUP.md](SALESFORCE_SETUP.md) for full setup. This
migration did not change any Salesforce extraction code, so its behavior is
unchanged from before -- only the destination (`salesforce_data` in SQLite
instead of Supabase) is new, and that destination is covered directly by
`tests/test_sqlite_ingest.py::test_ingest_rows_replace_existing_atomically_swaps_contents`
and `test_atomic_replace_never_leaves_table_empty_after_failed_ingest`.

## 10) Gemini live smoke test (opt-in, costs quota -- not run here)

```powershell
python scripts/check_google_auth.py --live --yes-i-want-to-call-gemini
```

or, for a full generation:

```powershell
$env:RUN_GEMINI_INTEGRATION_TESTS = "true"
python scripts/run_meeting_prep.py "Avery Benton"
```

**Not run in this environment** -- there is no Google Cloud project, ADC,
or billing configured here, and this is explicitly opt-in/gated so it can
never run by accident in CI or a normal dev loop. This is the only genuinely
untested path in the whole migration: the actual live request/response
shape against a real Gemini Enterprise endpoint. Everything up to and
including request construction, retry policy, and response parsing is
covered by the mocked test suite (section 6); only the live network
round-trip itself is unverified here.

## 11) Web application manual test walkthrough

The following was **actually run** against a real local server (not a
browser -- `curl` against the real running FastAPI app, with the sample
data actually ingested via `scripts/ingest_all.py --source csv` into a
scratch SQLite database, `DATA_SOURCE=csv` so no live Salesforce call was
made):

```
GET  /api/health                                  -> 200 {"status":"ok"}
GET  /api/advisors?q=aver&limit=5                  -> 200 {"advisors":["Avery Benton","Avery Oakley"]}
GET  /                                              -> 200 (home page HTML)
GET  /upload                                        -> 200 (upload page HTML)
POST /api/uploads/tableau (re-upload of the sample CSV)
                                                     -> 200 {"source_type":"tableau","file_name":"Tableau - DummyData.csv",
                                                             "rows_parsed":600,"rows_inserted":0,"rows_skipped_duplicate":600}
POST /api/meeting-prep {"advisor_name":"Avery Benton"} (no ADC configured)
                                                     -> 502 {"detail":"Meeting prep generation failed: Google Application
                                                             Default Credentials were not found or are invalid."}
```

This confirms: the server boots and serves pages, the SQLite-backed health
check and advisor search work against real ingested data, upload dedup
correctly reports 0 inserted / 600 skipped on a byte-identical re-upload
(the atomic `ON CONFLICT DO NOTHING` path), and a missing-ADC failure
degrades to a clean `502` with no prompt/credential content leaked in the
response -- rather than a crash or hang.

**Not performed in this environment** (no browser/display available): the
interactive browser walkthrough (typing in the advisor search box, clicking
dropdown entries, clicking "Get meeting prep document" and confirming a
`.docx` downloads, verifying the JS console has no errors). That walkthrough
is scripted and automatable via `tests/e2e_smoke_test.py` (Playwright) --
run it manually against a real Gemini-configured server:

```powershell
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
python scripts/run_server.py                # terminal 1
python tests/e2e_smoke_test.py               # terminal 2
```

## 12) CLI manual tests

Actually run in this environment, against a scratch SQLite database seeded
via `python scripts/ingest_all.py --source csv` with the real sample files
in `RelatedMaterials/Sample/`:

```
salesforce_data: 100 rows ingested
tableau_data: 600 rows ingested
consultant_scorecard_data: 200 rows ingested
consultant_scorecard_raw: 200 rows ingested
consultant_scorecard_raw_duplicates: 0 rows ingested
consultant_scorecard_monthly: 100 rows ingested
consultant_scorecard_metric: 6900 rows ingested
```

These counts match the same sample files' previously-verified Postgres/
Supabase ingestion counts exactly, which is strong evidence the SQLite
ingestion path is behaviorally equivalent. `scripts/query_advisor.py`,
`scripts/interactive_meeting_prep.py`, and `scripts/run_meeting_prep.py`
were not run against a live Gemini endpoint (no ADC here), but all share the
same `generate_meeting_prep()` function already covered by the mocked test
suite, and all correctly call `close_gemini_client()` in a `finally` block
(verified by `tests/test_gemini_llm.py::test_close_is_safe_when_client_never_created`
and `test_client_closes_during_shutdown`).

## 13) Expected database behavior

- **Fresh init**: `scripts/init_sqlite.py` on a nonexistent file creates
  `data/`, the `.sqlite3` file, all 8 application tables + `schema_migrations`,
  and 14 indexes.
- **Idempotent re-init**: running it again applies zero new migrations and
  changes nothing (`test_ensure_schema_ready_is_idempotent`).
- **Migrations recorded exactly once**, even across repeated
  `ensure_schema_ready()` calls (`test_migration_recorded_exactly_once`).
- **A failed migration rolls back completely** (including any
  already-executed `CREATE TABLE` statements within that same migration
  file) and is never recorded as applied, so a fixed retry starts clean
  (`test_failed_migration_rolls_back_and_is_not_recorded`).
- **Foreign keys are enforced** (`PRAGMA foreign_keys = ON` on every
  connection) and a violation raises `IntegrityError` and rolls back
  (`test_foreign_key_violation_is_rejected_and_rolled_back`).
- **Salesforce's full-table replace is atomic**: a failed replace attempt
  never leaves `salesforce_data` empty or partial -- the prior complete
  dataset survives (`test_atomic_replace_never_leaves_table_empty_after_failed_ingest`).
- **Tableau/scorecard-raw dedup** via `UNIQUE(content_hash)` + `ON CONFLICT
  DO NOTHING`; **scorecard-monthly upsert** via `UNIQUE(report_date,
  advisor_number)` + `ON CONFLICT DO UPDATE ... RETURNING`. Both verified
  with real parsed workbook data across two ingests of the same file.
- **Advisor search is case-insensitive** and **cross-source name bridging**
  (`"First Last"` <-> `"LAST, FIRST"`, and advisor-number fallback when
  spellings don't match at all) both work.
- **SQL injection as data is safe**: a value like
  `"Robert'); DROP TABLE salesforce_data;--"` round-trips as literal data
  via parameterized queries; the table is never dropped.
- **A locked database** (one connection holding `BEGIN IMMEDIATE`) causes a
  concurrent writer to raise `DatabaseLockedError` once
  `SQLITE_BUSY_TIMEOUT_MS` elapses, rather than hanging forever or silently
  losing the write.
- **The parent directory is auto-created** for a `SQLITE_DB_PATH` that
  doesn't exist yet.
- **Writes are transactional**: an exception raised inside `database.write()`
  rolls back everything written in that block.
- **`/api/health` detects DB unavailability**: pointed at an unusable path
  (a directory instead of a file), `health_check()` returns
  `{"ok": false, ...}` and the endpoint responds `503`.

## 14) Expected LLM behavior

- `generate_meeting_prep(prompt: str) -> str` keeps its exact pre-migration
  signature and contract (Markdown string in, Markdown string out, raises on
  failure) -- callers (`webapp/api.py`, all CLI scripts) needed no changes.
- The client is created with `enterprise=True`, the configured
  `project`/`location`, and `http_options=types.HttpOptions(api_version=...,
  timeout=...)` -- never an API key.
- Retries (bounded by `GOOGLE_GENAI_MAX_RETRIES`, exponential backoff +
  jitter) happen only for HTTP 429/500/502/503/504 and
  timeout/connection-reset errors; auth, permission, invalid-argument,
  not-found, and safety-block errors are raised immediately, never retried.
- An empty response raises `GeminiEmptyResponseError`; a non-`STOP` finish
  reason (safety block, recitation, etc.) with no text raises
  `GeminiSafetyBlockedError`.
- Logs include provider/project/location/model/api_version, prompt/response
  **character counts** (never content), duration, attempt count, and
  finish_reason/usage_metadata -- verified by
  `test_prompt_content_does_not_appear_in_logs`,
  `test_response_content_does_not_appear_in_logs`, and
  `test_credentials_do_not_appear_in_logs`.
- The web app creates one client at FastAPI startup and closes it at
  shutdown (`lifespan` in `webapp/app.py`); each CLI script creates and
  closes its own client per invocation.

## 15) Expected failure behavior

| Scenario | Result |
| --- | --- |
| ADC missing | `GeminiAuthError`; `check_google_auth.py` exits 2 with a message pointing at `gcloud auth application-default login`. Actually reproduced in section 4 above. |
| Wrong/nonexistent `GOOGLE_CLOUD_PROJECT` | HTTP 403/404 from the API -> `GeminiPermissionError`/`GeminiConfigError`; never retried. |
| Permission denied (IAM) | HTTP 403 -> `GeminiPermissionError`, not retried. |
| Vertex AI API not enabled on the project | HTTP 403 -> `GeminiPermissionError` (same as above; Google returns the same status for both). |
| Model not found / wrong `GOOGLE_GENAI_MODEL` | HTTP 404 -> `GeminiConfigError`, not retried. |
| Invalid `GOOGLE_CLOUD_LOCATION` | Rejected at config-load time if malformed, or HTTP 400/404 from the API -> `GeminiConfigError`. |
| TLS/certificate failure | Surfaces as a connection-layer exception; not explicitly reclassified, so it propagates as the underlying `google.genai`/`httpx` error (not retried unless it matches the timeout/connection-reset heuristic in `_is_retryable`). |
| Quota/rate limit (429) | Retried up to `GOOGLE_GENAI_MAX_RETRIES` times with backoff, then `GeminiQuotaError`. Reproduced by `test_rate_limit_errors_use_bounded_retries` / `test_rate_limit_raises_after_retries_exhausted`. |
| SQLite locked | `DatabaseLockedError` after `SQLITE_BUSY_TIMEOUT_MS`. Reproduced by `test_locked_database_raises_databaselockederror`. |
| SQLite path not writable / is a directory | `DatabaseNotWritableError`. Reproduced by `test_connect_raises_databasenotwritableerror_for_unusable_path`. |
| SQLite schema missing | `SchemaNotReadyError` from `require_schema_ready()` (used by `check_sqlite_tables.py`); `ensure_schema_ready()` (used at app startup) self-heals instead of raising. Reproduced by `test_require_schema_ready_raises_when_tables_missing`. |
| SQLite corrupt | `PRAGMA integrity_check` (run by `check_sqlite_tables.py`) reports the corruption; not independently reproduced here (would require hand-corrupting a real file). |
| Salesforce unavailable | Unchanged from before this migration -- `salesforce/client.py`'s existing auth/retry/error handling (not touched by this migration). |
| Invalid upload (wrong extension, empty file, >25MB) | `webapp/api.py` returns `400` before any parsing/DB work happens; unchanged from before this migration. |

## 16) Troubleshooting

See the "Troubleshooting" table in [README.md](../README.md#troubleshooting)
for the quick-reference version. All entries there map directly to the typed
exceptions in section 15 above.

## 17) Cleanup and reset

There is **no destructive command anywhere in this migration** --
`scripts/init_sqlite.py` and `ensure_schema_ready()` only ever create
missing tables/indexes/migrations, never drop or delete. To genuinely start
over, you must act directly on the file system (destructive -- back up
first if the data matters):

```powershell
# DESTRUCTIVE: deletes all local application data. Not reversible.
Remove-Item data\dvp_meeting_prep.sqlite3, data\dvp_meeting_prep.sqlite3-wal, data\dvp_meeting_prep.sqlite3-shm -ErrorAction SilentlyContinue
python scripts/init_sqlite.py   # recreates an empty schema
```

```bash
# DESTRUCTIVE (WSL/Linux/macOS equivalent)
rm -f data/dvp_meeting_prep.sqlite3 data/dvp_meeting_prep.sqlite3-wal data/dvp_meeting_prep.sqlite3-shm
python scripts/init_sqlite.py
```

For Docker, the equivalent is removing the named volume (**destructive**):

```powershell
docker compose down -v   # DESTRUCTIVE: also deletes the sqlite_data volume
```

To revoke local Google ADC credentials:

```powershell
gcloud auth application-default revoke
```

## 18) Acceptance checklist

- [x] `llm.py`'s `generate_meeting_prep(prompt) -> str` contract unchanged.
- [x] No OpenAI/Gemini/Google API key anywhere in the codebase; ADC only.
- [x] Application code never shells out to `gcloud` or handles a raw access token.
- [x] `enterprise=True`, project/location/api_version passthrough, per-request generation config all covered by tests.
- [x] Bounded retry only for 429/5xx/timeout/connection-reset; auth/permission/config/safety errors never retried.
- [x] No prompt, response, Salesforce note, credential, or token ever appears in a log line (tested).
- [x] `scripts/check_google_auth.py` never makes a paid call unless `--live` + explicit confirmation.
- [x] Client created once at FastAPI startup, closed at shutdown; CLI scripts create/close per invocation.
- [x] All 8 logical application tables preserved with equivalent columns/keys/indexes in SQLite.
- [x] `sqlite3` stdlib only; no SQLAlchemy/aiosqlite added.
- [x] `SQLITE_DB_PATH` resolves relative to the project root, not CWD; parent directory auto-created.
- [x] `.gitignore` excludes real `.sqlite3`/WAL/SHM files; `data/.gitkeep` tracked.
- [x] `PRAGMA foreign_keys/busy_timeout/journal_mode/synchronous` set on every connection; `row_factory = sqlite3.Row`.
- [x] All SQL parameterized; adversarial SQL-as-data verified safe.
- [x] Migration runner applies each migration exactly once, inside a transaction, rolling back (and not recording) on failure.
- [x] `ensure_schema_ready()` never drops/deletes/resets; no automatic destructive behavior anywhere.
- [x] `scripts/init_sqlite.py`, `scripts/check_sqlite_tables.py`, `scripts/setup_sqlite.ps1`, `scripts/setup_sqlite.sh` all implemented and directly run.
- [x] Salesforce full-table replace atomic; verified rollback-on-failure leaves prior data intact.
- [x] Tableau/scorecard dedup preserved via unique indexes + `ON CONFLICT`, no SELECT-then-INSERT.
- [x] Advisor search/autocomplete, cross-format name bridging, deterministic ordering all preserved and tested.
- [x] Docker: SQLite persists via a mounted volume across restarts (directly verified, see README's Docker section); non-root user owns the volume; no baked-in DB/credentials; schema initialized safely before serving; `/api/health` checks DB readiness; no Supabase env assumptions remain; local ADC mount documented as dev-only.
- [x] Normal test suite never contacts a live external service (verified: no network calls in any of the 166 tests).
- [x] `python -m pytest tests/`, `python -m pyflakes ...`, a real SQLite init/verify, and a mocked end-to-end run were all actually executed, with real results recorded above.
- [x] OpenAI and Supabase/Postgres dependencies, config, and docs fully removed (not just unused) -- confirmed via repo-wide sweep.

## 19) Known limitations / unresolved values

- **No live Gemini call was made anywhere in this work.** The request/
  response shape against a real Gemini Enterprise endpoint (exact latency,
  exact `usage_metadata` fields, exact behavior of a real safety block) is
  unverified in this environment. Run
  `python scripts/check_google_auth.py --live --yes-i-want-to-call-gemini`
  against a real project before relying on this in production.
- **No live Salesforce call was made anywhere in this work** (this
  migration didn't touch Salesforce extraction code, so this is a
  pre-existing limitation, not a new one introduced here).
- **TLS/certificate-failure handling is not independently exercised** --
  it falls through to the generic `GeminiError`/`_is_retryable` name-based
  heuristic rather than a dedicated typed exception; if this turns out to
  matter in practice (e.g. a corporate TLS-inspecting proxy), it would be
  worth adding an explicit classification.
- **SQLite corruption handling is not independently exercised** -- the
  `check_sqlite_tables.py` script runs `PRAGMA integrity_check` and would
  report a real corruption, but this environment never actually corrupted a
  real file to test that path live.
- **No browser-based manual walkthrough was performed** (no display
  available in this environment) -- `tests/e2e_smoke_test.py` exists and is
  ready to run manually; only its HTTP-level equivalent was actually
  exercised here (section 11).
- `GOOGLE_GENAI_MODEL` in the example env files (`gemini-2.0-flash-001`) is
  a placeholder -- confirm the actual model name/availability for your
  Google Cloud project before deploying.
- No destructive `--reset` flag exists anywhere in the new SQLite tooling
  (intentional, per the "never delete data automatically" requirement) --
  if a genuine reset workflow is needed later, it should be added as an
  explicitly double-gated flag rather than assumed to already exist.
