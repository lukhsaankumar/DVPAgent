# DVP Meeting Prep

An internal tool that assembles advisor context from Salesforce, Tableau, and
consultant scorecard data, and generates a downloadable Word document to prep
for DVP (District Vice President) advisor meetings using an LLM.

It has two ways to work with it:

- **Web app** (recommended): search for an advisor, click a button, download
  a formatted `.docx` meeting prep document. Upload new Tableau exports or
  consultant scorecards through a browser page.
- **CLI scripts**: the original terminal-driven workflow, still available for
  bulk ingestion and scripted/automated use.

## How it works

```
Salesforce export (.xlsx) ─┐
Tableau export (.csv)      ├─> parse & ingest ─> Supabase (Postgres) ─> query by advisor ─> LLM prompt ─> Markdown ─> .docx
Consultant scorecard (.xlsx)┘
```

Today, Salesforce and Tableau data get into Supabase via `scripts/ingest_all.py`
(bulk load of exported files). Consultant scorecards and Tableau exports can
also be uploaded one at a time through the web app's `/upload` page. **A
future update will pull Salesforce data directly from a Salesforce sandbox
instead of a manual export** — the ingestion and database layers are already
structured so that swapping the Salesforce source in is a matter of adding a
new ingestion path, not rebuilding anything downstream.

All uploads are dedup-safe: re-uploading the same file (or one with
overlapping rows) never creates duplicate rows. Rows are matched by a content
hash (Tableau, consultant scorecard raw rows) or by natural key (consultant
scorecard monthly/metric rows upsert on report date + advisor number).

## Project layout

```
src/dvp_meeting_prep/
  config.py          Environment/settings loading (.env)
  db.py               Supabase client
  files.py            Parsers for Salesforce/Tableau/scorecard files
  ingest.py           Ingestion + dedup logic
  query.py            Fetch a single advisor's rows across all source tables
  prompting.py        Builds the LLM prompt payload from source rows
  llm.py               OpenAI call
  advisors.py         Advisor name search/autocomplete (for the web UI)
  docx_export.py      Markdown -> Word document conversion
  webapp/
    app.py             FastAPI app (serves the UI + mounts the API router)
    api.py             REST endpoints: /api/advisors, /api/uploads/*, /api/meeting-prep
    static/            Home page, upload page, CSS, vanilla JS (no build step)

scripts/               CLI entry points (see "CLI scripts" below)
sql/schema.sql         All table/index/view DDL - the source of truth for the DB schema
tests/e2e_smoke_test.py Playwright browser smoke test (manual, hits real APIs)
RelatedMaterials/Sample/ Sample source files used for local testing (gitignored)
Dockerfile, docker-compose.yml  Containerized runtime (see "Run with Docker" below)
```

## 1) Setup

**Prerequisites:** Python 3.12+, a Supabase project, an OpenAI API key.

```powershell
# Create/activate a virtual environment
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1

# Install dependencies (requirements-dev.txt adds pytest + playwright, for
# tests/e2e_smoke_test.py -- not needed just to run the app)
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in real values:

- `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY` — from
  Supabase Project Settings > API.
- `SUPABASE_URI` — from Project Settings > Database > Connection string
  (used only by `scripts/run_schema.py`, which applies DDL directly).
- `OPENAI_API_KEY`, `OPENAI_MODEL` — your OpenAI key and model name.

Create/update the database tables:

```powershell
python scripts/run_schema.py
```

`sql/schema.sql` is additive (`create table if not exists`, `add column if
not exists`) so it's safe to rerun after pulling schema changes.

**If you're adding the dedup columns to a database that already has rows**
from before this change (i.e. rows with no `content_hash` set), run the
one-time backfill so existing rows are matched correctly by future uploads
instead of getting duplicated:

```powershell
python scripts/backfill_content_hashes.py
```

## 2) Run the web app

```powershell
python scripts/run_server.py
```

Then open http://127.0.0.1:8000 in a browser.

- **Home page (`/`)** — start typing an advisor name; matching advisors
  (sourced from Salesforce + Tableau data already in the database) appear in
  a dropdown, shown in caps for readability. Click one, then click "Get
  meeting prep document" to generate and download a `.docx` file. The
  generated Markdown and prompt are also logged to the
  `meeting_prep_documents` table for audit purposes.
- **Upload page (`/upload`)** — upload a consultant scorecard workbook or a
  Tableau CSV export. Each upload reports how many rows were parsed, how many
  were newly added, and how many were already present (skipped as
  duplicates). Salesforce is intentionally not uploadable here — see the
  roadmap note above.

`--host` / `--port` flags are available if you need to bind elsewhere:
`python scripts/run_server.py --host 0.0.0.0 --port 8080`.

### API reference

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Liveness check |
| GET | `/api/advisors?q=<prefix>&limit=20` | Advisor name search |
| POST | `/api/uploads/tableau` | Upload a Tableau CSV (`multipart/form-data`, field `file`) |
| POST | `/api/uploads/consultant-scorecard` | Upload a scorecard `.xlsx` (`multipart/form-data`, field `file`) |
| POST | `/api/meeting-prep` | `{"advisor_name": "..."}` -> streams back a `.docx` |

## 3) Run with Docker

An alternative to the local venv setup above. The image runs the web app
only — Supabase and OpenAI are still external, reached over the network, so
there's no database container to run alongside it. Requires steps in
"1) Setup" up through having a filled-in `.env` (the schema still needs to
exist in Supabase before the app is useful; `scripts/run_schema.py` runs
locally against `SUPABASE_URI`, not from inside the container).

```powershell
docker compose up --build
```

Then open http://127.0.0.1:8000, same as running locally. `docker-compose.yml`
reads `.env` via `env_file`, so nothing needs to be baked into the image or
passed on the command line. Stop it with `docker compose down`.

Without Compose:

```powershell
docker build -t dvp-meeting-prep .
docker run --rm -p 8000:8000 --env-file .env dvp-meeting-prep
```

Notes:
- The image installs only `requirements.txt` (runtime deps) — Playwright and
  its browser binaries are never pulled into it.
- Runs as a non-root user, and `HEALTHCHECK` hits `/api/health`
  (`docker ps` / `docker inspect` will show `healthy`/`unhealthy`).
- One-off scripts (e.g. re-running ingestion) can be run in the same image:
  `docker compose run --rm web python scripts/ingest_all.py`.
- The `.env` file is never copied into the image (see `.dockerignore`) — it's
  only ever injected at container start, so rebuilding the image doesn't
  require secrets and the image itself doesn't contain them.

## 4) CLI scripts

Still useful for bulk-loading sample data or scripted runs without the UI.

```powershell
# Bulk-ingest all three sample sources (replaces existing rows by default)
python scripts/ingest_all.py

# Verify REST access to every table
python scripts/check_supabase_tables.py

# Quick advisor query (prints raw rows from every source table)
python scripts/query_advisor.py "Avery Benton"

# Interactive flow: pick an advisor, preview the prompt, optionally generate
python scripts/interactive_meeting_prep.py --query-output output/query_results_avery.txt --prompt-output output/prompt_preview_avery.txt

# Direct generation, saved straight to a Markdown file
python scripts/run_meeting_prep.py "Avery Benton" --save output/avery_benton_meeting_prep.md
```

Note: the CLI scripts save Markdown, not `.docx` — Word export is currently
only available through the web app's `/api/meeting-prep` endpoint.

## 5) Testing

There's no mocked test suite (the app is a thin layer over Supabase and
OpenAI, so meaningful tests need the real thing). Instead:

- `python -m pyflakes src/dvp_meeting_prep scripts tests` — static check for
  unused imports/variables.
- `tests/e2e_smoke_test.py` — a Playwright browser test that drives the real
  UI end to end (search -> select -> generate -> download -> verify the
  `.docx`; upload a Tableau CSV and a scorecard; verify empty-search state;
  fail on any JS console error). It hits the real Supabase project and
  OpenAI API configured in `.env`, so it costs money and is not part of an
  automated CI run — run it manually after starting the server:

  ```powershell
  python -m pip install -r requirements-dev.txt
  python -m playwright install chromium
  python scripts/run_server.py                # in one terminal
  python tests/e2e_smoke_test.py               # in another
  ```

## Data model

See `sql/schema.sql` for the full DDL. Summary:

| Table | Written by | Read by |
| --- | --- | --- |
| `salesforce_data` | `scripts/ingest_all.py` | advisor search, meeting prep |
| `tableau_data` | `scripts/ingest_all.py`, `/api/uploads/tableau` | advisor search, meeting prep |
| `consultant_scorecard_raw` | `scripts/ingest_all.py`, `/api/uploads/consultant-scorecard` | audit trail only |
| `consultant_scorecard_monthly` | same as above | meeting prep |
| `consultant_scorecard_metric` | same as above | meeting prep |
| `consultant_scorecard_data` | `scripts/ingest_all.py` | legacy flat mirror of the scorecard, kept for the sample health-check script; not read by the meeting prep flow |
| `upload_batches` | `/api/uploads/*` | audit trail of uploads made through the UI |
| `meeting_prep_documents` | `/api/meeting-prep` | audit trail of generated documents |

Advisor identity note: Salesforce/Tableau store names as `"First Last"`;
consultant scorecard rows store `"LAST, FIRST"` in caps. `query.py` bridges
this by trying both forms when looking up a consultant scorecard row, so the
web UI only needs to show/search the Salesforce/Tableau form.

## Roadmap

1. Replace the Salesforce spreadsheet export with a direct read from a
   Salesforce sandbox (then production) instance.
2. ~~Web UI for uploads and advisor search/generation~~ (done).
3. Broader audit + productionization once the above two are stable.
