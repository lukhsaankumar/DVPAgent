# Salesforce data source setup

This app can pull advisor/task data directly from Salesforce instead of the
local `.xlsx` export. This doc covers setup, discovery, and troubleshooting.
For everything else (SQLite, Gemini, the web app, Docker), see the main
[README](../README.md).

## How it fits together

```
scripts/ingest_all.py, scripts/salesforce_extract.py
        |
        v
data_source.py: load_salesforce_source_data()   <- DATA_SOURCE picks the branch
        |                        |
        v                        v
salesforce/extraction.py    files.py: read_salesforce_rows()  (DATA_SOURCE=csv)
  connect -> describe -> query
  advisors/tasks/opportunities
  -> normalize -> validate
        |
        v
   same row shape either way (see salesforce/normalize.py:LEGACY_ROW_COLUMNS)
        |
        v
ingest.py: ingest_rows(..., "salesforce_data", ...) -> SQLite
```

Downstream code (ingestion, prompting, the web app) never branches on which
source produced the rows -- `data_source.py` is the only place `DATA_SOURCE`
is read for this purpose.

## 1) Install dependencies

```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

This installs `simple-salesforce` along with everything else (see
`requirements.txt`; `simple-salesforce>=1.12.6`).

## 2) Copy an environment file

```powershell
Copy-Item .env.sandbox.example .env.sandbox
```

Edit `.env.sandbox` with real values. It's loaded automatically because
`APP_ENV=sandbox` maps to `.env.sandbox` by convention (see "Environment
files" below). Never commit `.env.sandbox` itself -- only the `*.example`
templates are tracked (see `.gitignore`).

## 3) Configure sandbox credentials

Minimum required for `SF_AUTH_MODE=password` (the default, and the
recommended mode for a sandbox/CLI use -- access tokens expire quickly and
nothing here refreshes them):

```
SF_AUTH_MODE=password
SF_USERNAME=your-sandbox-username
SF_PASSWORD=your-sandbox-password
SF_SECURITY_TOKEN=your-security-token
```

The security token resets whenever the password changes (Salesforce emails a
new one). `APP_ENV=sandbox` makes the app authenticate against the `test`
domain (`test.salesforce.com`); `APP_ENV=production` uses `login`; the
Lightning **sandbox URL you were given
(`https://igdigitalplatform--ahackathon.sandbox.lightning.force.com/one/one.app`)
is a browser UI, not an API endpoint** -- don't put it in `SF_INSTANCE_URL`.
`simple-salesforce` derives the correct API host itself from
`SF_AUTH_MODE`/`APP_ENV`; you never construct that URL by hand for password
auth. `SF_INSTANCE_URL` is only used for `SF_AUTH_MODE=access_token` (an
existing `session_id`/`instance_url` pair) or `APP_ENV=custom`.

## 4) Run metadata discovery

**Run this first, before switching `DATA_SOURCE=salesforce` for real.** It
connects and reports the actual API names in your org so you can fix the
placeholder guesses below.

```powershell
python scripts/salesforce_extract.py --discover-salesforce
```

What it does (regardless of the configured `DATA_SOURCE` -- discovery always
talks to Salesforce directly, since its purpose is finding names *before* you
rely on them):

1. Runs a global describe and lists objects whose name/label contain
   "advisor", "practice", "number", "wholesaler", or "territory" -- candidates
   for `SF_ADVISOR_OBJECT` / `SF_PRACTICE_LOOKUP_FIELD`.
2. Describes `SF_ADVISOR_OBJECT` (default `Account`), `Task`, `Opportunity`,
   `Account`, and `Contact`, listing fields matching those same keywords and
   every lookup field with its `referenceTo` target.
3. Validates every configured field (`SF_ADVISOR_NUMBER_FIELD`,
   `SF_PRACTICE_LOOKUP_FIELD`, `SF_TASK_LINK_FIELD`,
   `SF_OPPORTUNITY_LINK_FIELD`, every `SF_ADVISOR_FIELD_MAP`/
   `SF_TASK_FIELD_MAP` target, every `*_EXTRA_FIELDS` entry) against the real
   describe results and prints `OK` or `MISSING (reason)` for each -- with a
   close-match suggestion (e.g. "did you mean 'Advisor_Number__c'?") when a
   configured name is almost right.

### Determining the correct object/field API names

Start from what discovery prints under "objects whose name/label look
advisor/practice-related" and "Fields matching advisor/practice/number/..."
-- in most orgs the Advisor object is a custom object or `Account` with a
custom `Advisor_Number__c`-style field, and Tasks relate to it via `WhatId`
(if Advisors are stored as Accounts) or a custom lookup (if Advisors are a
separate object from whatever Tasks/Opportunities actually relate to). If
Advisors roll up to a separate **Practice** record, set
`SF_PRACTICE_LOOKUP_FIELD` to the lookup field on the Advisor object that
points at it -- Tasks/Opportunities will then be queried by Practice ID
instead of Advisor ID. Leave it blank if Tasks/Opportunities relate directly
to the Advisor record itself.

## 5) Run a dry run

```powershell
python scripts/salesforce_extract.py --dry-run
```

Connects, validates metadata, runs all three queries (advisors, tasks,
opportunities), normalizes, and prints `[VALIDATION]` results -- without
writing to the database or generating any meeting prep document. Use this to
confirm the expected counts before doing a real ingest:

```
[VALIDATION] Advisors: expected=5 actual=5 status=PASS
[VALIDATION] Tasks: expected=83 actual=83 status=PASS
[VALIDATION] Opportunities: expected=4 actual=4 status=PASS
```

## 6) Run the full extraction

```powershell
python scripts/salesforce_extract.py
```

Same as the dry run, but also writes the resulting rows into the
`salesforce_data_auto` SQLite table (replacing existing rows). This is what
"Salesforce is the default data source" means in practice --
`scripts/ingest_all.py` calls the same underlying pipeline for its
Salesforce portion whenever `DATA_SOURCE=salesforce` (the default), also
writing to `salesforce_data_auto`. The CSV fallback (`DATA_SOURCE=csv`)
writes to the separate `salesforce_data` table instead -- see
`ADVISOR_SOURCE_MODE` in `.env.example` for which one the app actually reads
from when searching for/generating prep on an advisor.

To force the local `.xlsx` fallback instead, either for one run or as your
default:

```powershell
# one-off override
python scripts/salesforce_extract.py --source csv
DATA_SOURCE=csv python scripts/ingest_all.py

# persistent: set in your env file
DATA_SOURCE=csv
```

## 7) DEBUG logging

```
LOG_LEVEL=DEBUG
SF_DEBUG=true
```

`LOG_LEVEL=DEBUG` prints the full SOQL for every query (`logger.debug`,
never printed at INFO). `SF_DEBUG=true` additionally runs a Task-subject
aggregate audit query (`SELECT Subject, COUNT(Id) ... GROUP BY Subject`)
against the resolved scope before the real Task query, so you can see what
Subject values actually exist before the exact `Subject IN ('Call','Virtual
Meeting')` filter is applied. The audit is informational only -- the real
extraction always applies the exact configured filter regardless.

`SF_SAVE_DEBUG_EXTRACTS=true` additionally writes a JSON snapshot of the raw
advisor/task/opportunity records to `.salesforce_debug/` (gitignored). Free
text fields (`Description`, `NextStep`) are redacted even there. Leave this
off unless you're actively debugging a field-mapping problem -- it can
contain real advisor/account data.

## 8) Interpreting expected-count warnings

```
SF_EXPECTED_ADVISOR_COUNT=5
SF_EXPECTED_TASK_COUNT=83
SF_EXPECTED_OPPORTUNITY_COUNT=4
SF_STRICT_EXPECTED_COUNTS=false
```

With `SF_STRICT_EXPECTED_COUNTS=false` (the default), a count mismatch logs
`[VALIDATION] ... status=MISMATCH` as a warning and the run continues --
useful while you're still narrowing down field names, since one wrong
mapping shouldn't block iterating. Set it to `true` once the counts are
known-stable (e.g. in production) so a broken integration fails loudly
instead of silently ingesting partial/wrong data.

Other validation checks (always run, same strict/non-strict behavior):
missing advisor numbers, duplicate advisor-number mappings, duplicate
Task/Opportunity IDs, Tasks with a Subject outside `SF_TASK_SUBJECTS`, and
Task/Opportunity records outside the resolved advisor/practice scope.

## 9) Archived Tasks

Tasks are queried with `include_deleted=True`
(`sf.query_all(soql, include_deleted=True)`) while the SOQL itself always
keeps `IsDeleted = FALSE`. Salesforce's `include_deleted` flag is what makes
**archived-but-not-deleted** Activities visible to the query in the first
place; the `IsDeleted = FALSE` condition then excludes the ones that were
actually deleted. Together: archived, non-deleted Tasks are included;
deleted Tasks never are.

## 10) Why `/one/one.app` isn't an API endpoint

`https://igdigitalplatform--ahackathon.sandbox.lightning.force.com/one/one.app`
is the Lightning Experience *browser UI* -- it renders an authenticated
session in a browser and has no stable JSON contract to script against.
This integration authenticates with `simple-salesforce` (username/password/
security token, or an existing session) and talks to Salesforce's REST/SOQL
APIs directly; it never loads or scrapes that URL.

## 11) Troubleshooting access

**"Object not found or not accessible"** (raised as `SalesforceMetadataError`)
during discovery or extraction: the integration user's profile/permission set
doesn't have **Read** (Object-Level Security) on that object, or the object
API name is wrong -- rerun `--discover-salesforce` to check the name against
what's actually queryable.

**A field is silently missing from results, or discovery reports it
`MISSING`**: check Field-Level Security (FLS) for the integration user's
profile on that field, not just object-level access -- a field can be
object-readable but still hidden per-field. Note that Salesforce's describe
API doesn't expose a clean separate "can this user query this field" flag
distinct from "does this field exist" (see the docstring on
`select_available_fields` in `salesforce/metadata.py`); a field that exists
in describe() but is denied by FLS may still come back `null` rather than
causing an error.

**"API Enabled" permission**: the integration user's profile/permission set
needs the "API Enabled" system permission, or every API call (including
authentication) fails with an authorization error. This is a per-profile
checkbox in Salesforce setup, separate from object/field permissions.

**Authentication fails in password mode**: the security token resets when
the password changes -- if you rotated the password, request a new token.
Also confirm `APP_ENV` matches where the user actually lives (a sandbox
username with `APP_ENV=production`, or vice versa, authenticates against the
wrong domain and fails).

## 12) What must never be committed

- `.env`, `.env.sandbox`, `.env.production`, `.env.local` (anything matching
  `.env.*` except the `*.example` templates) -- see `.gitignore`.
- `SF_PASSWORD`, `SF_SECURITY_TOKEN`, `SF_ACCESS_TOKEN`, or any value derived
  from them. The app never logs or prints these -- only
  `SF_USERNAME configured: true`-style booleans (see
  `salesforce/client.py:_log_credential_presence`).
- `.salesforce_debug/` (gitignored) -- only created when
  `SF_SAVE_DEBUG_EXTRACTS=true`, and even then free-text note fields are
  redacted, but it can still contain real advisor/account data.

## Full environment variable reference

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATA_SOURCE` | `salesforce` | `salesforce` or `csv` (local `.xlsx` fallback) |
| `APP_ENV` | `sandbox` | `sandbox`, `production`, or `custom` -- picks the auth domain and, with `ENV_FILE`, which dotenv file loads |
| `ENV_FILE` | *(derived from APP_ENV)* | Explicit dotenv file to load; overrides the APP_ENV convention |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `SF_DEBUG` | `false` | Run the Task-subject audit query before the real Task query |
| `SF_DEBUG_SAMPLE_SIZE` | `3` | Reserved for sampling debug output (console samples currently print a fixed small set; kept for future tuning) |
| `SF_SAVE_DEBUG_EXTRACTS` | `false` | Write a redacted JSON snapshot to `.salesforce_debug/` |
| `SF_STRICT_EXPECTED_COUNTS` | `false` | Raise instead of warn on count/validation mismatches |
| `SF_AUTH_MODE` | `password` | `password` or `access_token` |
| `SF_USERNAME` / `SF_PASSWORD` / `SF_SECURITY_TOKEN` | *(none)* | Password-mode credentials |
| `SF_ACCESS_TOKEN` / `SF_INSTANCE_URL` | *(none)* | Access-token-mode credentials (`session_id` / `instance_url`) |
| `SF_ADVISOR_OBJECT` | `Account` | The Advisor/Practice sObject API name |
| `SF_ADVISOR_NUMBER_FIELD` | `Advisor_Number__c` | Field on `SF_ADVISOR_OBJECT` holding the advisor number |
| `SF_PRACTICE_LOOKUP_FIELD` | *(blank)* | Lookup field to a separate Practice object, if Advisors have one; blank uses the Advisor's own Id |
| `SF_TASK_LINK_FIELD` | `WhatId` | Field on Task linking it to the resolved scope (Advisor or Practice Id) |
| `SF_OPPORTUNITY_LINK_FIELD` | `AccountId` | Field on Opportunity linking it to the resolved scope |
| `SF_ADVISOR_NUMBERS` | `17018,34318,34605,21114,20728` | Comma-separated advisor numbers to resolve |
| `SF_TASK_SUBJECTS` | `Call,Virtual Meeting` | Comma-separated exact Task Subject filter values |
| `SF_ACTIVITY_START_DATE` | *(blank = no filter)* | ISO `YYYY-MM-DD`; adds an `ActivityDate >=` filter when set |
| `SF_EXPECTED_ADVISOR_COUNT` / `_TASK_COUNT` / `_OPPORTUNITY_COUNT` | `5` / `83` / `4` | Expected result counts for validation |
| `SF_ADVISOR_EXTRA_FIELDS` / `SF_TASK_EXTRA_FIELDS` / `SF_OPPORTUNITY_EXTRA_FIELDS` | *(blank)* | Additional fields to pull beyond the built-in preferred list |
| `SF_ADVISOR_FIELD_MAP` / `SF_TASK_FIELD_MAP` | *(see below)* | `legacy_column=Real_API_Name__c,...` overrides for legacy-contract columns without an obvious standard-field equivalent |
| `CSV_INPUT_PATH` | *(sample file)* | Path to the `.xlsx` export, used only when `DATA_SOURCE=csv` |

### Default `SF_ADVISOR_FIELD_MAP` / `SF_TASK_FIELD_MAP`

These two variables exist beyond what a first pass at this integration might
assume, because several legacy `salesforce_data` columns
(`district_vp_wholesaling`, `pwm`, `book_size`, `assets_under_management`,
`new_business_ytd`, `area`, `region_office_number`, `start_date`, `assigned`)
don't correspond to any standard Salesforce field -- they were presumably
custom fields (or Practice-level roll-ups) in whatever org originally
produced the `.xlsx` export this app was built against. Rather than hardcode
a guess with no way to correct it short of a code change, every one of these
is a configurable mapping with a best-guess default:

```
district_vp_wholesaling -> District_VP_Wholesaling__c
pwm                      -> PWM__c
book_size                -> Book_Size__c
assets_under_management  -> Assets_Under_Management__c
new_business_ytd         -> New_Business_YTD__c
area                     -> Area__c
region_office_number     -> Region_Office_Number__c
start_date               -> Start_Date__c
assigned                 -> Owner.Name

task_subtype        -> TaskSubtype        (real standard field, high confidence)
subject              -> Subject            (real standard field, high confidence)
comments              -> Description        (real standard field, high confidence)
completed_date_time    -> CompletedDateTime   (real standard field, high confidence)
created_date             -> CreatedDate         (real standard field, high confidence)
status                     -> Status              (real standard field, high confidence)
interaction_type            -> Type                (no clean standard equivalent -- lowest-confidence guess)
```

Run `--discover-salesforce` to check each of these against your org (it
validates every configured field-map target and reports `MISSING` with a
suggestion if it's wrong); override individual entries without restating the
whole map, e.g.:

```
SF_ADVISOR_FIELD_MAP=pwm=Is_PWM__c,book_size=Total_Book_Size__c
```

A field that isn't found is **omitted from the query, not fatal** -- it
comes back `null` in the resulting row and a `[warning]` names which field
and why (see "Preferred field selection" in `salesforce/queries.py`).

## Environment files

| File | Loaded when | Committed? |
| --- | --- | --- |
| `.env` | Always, in addition to whatever `ENV_FILE`/`APP_ENV` resolve to | No (`.gitignore`) |
| `.env.sandbox` | `APP_ENV=sandbox` (default) or `ENV_FILE=.env.sandbox` | No |
| `.env.production` | `APP_ENV=production` or `ENV_FILE=.env.production` | No |
| `.env.sandbox.example`, `.env.production.example`, `.env.example` | Never loaded automatically -- copy to the real filename | Yes |

Precedence (highest to lowest): **already-exported OS environment
variables** > **the selected `ENV_FILE`/`APP_ENV` dotenv file** > **hardcoded
defaults in `config.py`**. `python-dotenv` is always called with
`override=False`, so a real environment variable is never clobbered by a
dotenv file -- this is what makes `DATA_SOURCE=csv python scripts/ingest_all.py`
work as a one-off override even with `DATA_SOURCE=salesforce` sitting in
`.env.sandbox`.

## Assumptions worth knowing about

- **The legacy `salesforce_data` row is a Task, denormalized with its
  Advisor's fields** (one row per Task, not per Advisor) -- that's what the
  original `.xlsx`-based `read_salesforce_rows()` produced, and it's the
  contract this integration preserves exactly (see
  `salesforce/normalize.py:LEGACY_ROW_COLUMNS`).
- **Opportunities are fully queried, normalized, and validated, but are not
  part of that legacy contract** -- there's no existing downstream table or
  consumer for them (the original CSV-based pipeline never had Opportunity
  data at all). They're available on the `SalesforceExtractionResult`
  returned by `run_extraction()` (`.opportunities`) for future use; wiring
  them into a new table/consumer is a follow-up, not something this change
  invents a destination for.
- **A Task that matches no resolved advisor/practice scope is reported, not
  silently dropped or inserted with a null advisor** -- `advisor_name`/
  `advisor_number` are `NOT NULL` in `salesforce_data`, so there's no way to
  represent an orphaned Task in that schema; it shows up in
  `NormalizationResult.dropped` with a reason instead.
- **A Practice shared by multiple Advisors fans out**: if
  `SF_PRACTICE_LOOKUP_FIELD` is set and two Advisor numbers resolve to the
  same Practice ID, a Task for that Practice produces one legacy row per
  Advisor sharing it, rather than arbitrarily picking one.
