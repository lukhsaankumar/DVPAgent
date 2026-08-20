-- DVP Meeting Prep -- SQLite schema (active database, replacing the archived
-- Postgres/Supabase schema in sql/postgres_schema_legacy.sql).
--
-- This file is the complete, idempotent schema for a fresh database: every
-- statement uses IF NOT EXISTS, so running it against an already-current
-- database is a no-op that never drops or deletes anything. It is applied by
-- scripts/init_sqlite.py, which also records it as migration 0001 in
-- schema_migrations (see sql/migrations/0001_initial.sql).
--
-- Translation notes from the original Postgres schema (see
-- docs/TESTING_GEMINI_SQLITE_MIGRATION.md for the full migration writeup):
--   * bigint generated always as identity  -> INTEGER PRIMARY KEY (SQLite
--     rowid alias; AUTOINCREMENT is intentionally omitted -- it is stricter
--     and slower than plain rowid reuse-avoidance, which nothing here needs)
--   * jsonb                                -> TEXT (JSON-encoded)
--   * boolean                              -> INTEGER (0/1)
--   * timestamptz / date                   -> TEXT (UTC ISO-8601)
--   * numeric                              -> REAL
--   * schema-qualified public.*            -> unqualified (SQLite has no schemas)
--   * consultant_scorecard_metrics_v (a Postgres view using jsonb_each_text
--     and regexp_replace, both Postgres-only) is dropped: it was never read
--     by any application code (confirmed by repository-wide search), and its
--     Postgres-specific SQL has no direct SQLite equivalent worth building
--     for an unused view.

CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at TEXT NOT NULL
);

-- One row per .xlsx "WS Team CRM History" line -- the "legacy" source,
-- populated only when DATA_SOURCE=csv. Kept separate from
-- salesforce_data_auto (below) because the two are pulled from different
-- places (a manually-provided spreadsheet vs. the live Salesforce API) and
-- have been found to disagree on which advisors/records exist -- see
-- salesforce_data_auto's comment. Fully replaced on each ingest run -- see
-- db.py replace_table_atomic().
CREATE TABLE IF NOT EXISTS salesforce_data (
  id INTEGER PRIMARY KEY,
  advisor_name TEXT NOT NULL,
  advisor_number TEXT,
  task_subtype TEXT,
  subject TEXT,
  comments TEXT,
  interaction_type TEXT,
  completed_date_time TEXT,
  district_vp_wholesaling TEXT,
  pwm TEXT,
  book_size TEXT,
  assets_under_management TEXT,
  new_business_ytd TEXT,
  created_date TEXT,
  start_date TEXT,
  status TEXT,
  area TEXT,
  region_office_number TEXT,
  assigned TEXT,
  raw_payload TEXT,
  ingested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS salesforce_data_advisor_name_idx
  ON salesforce_data (advisor_name);

-- Same shape as salesforce_data, but populated only from a live Salesforce
-- extraction (DATA_SOURCE=salesforce, i.e. scripts/salesforce_extract.py or
-- scripts/ingest_all.py without --source csv). Deliberately a separate
-- table, not a merge into salesforce_data: the live sandbox and the manual
-- legacy spreadsheet have been found to disagree on which advisors actually
-- exist (some accounts in the live org are anonymized/seed data, not real
-- advisors), so keeping them apart lets ADVISOR_SOURCE_MODE pick one
-- explicitly instead of silently blending a possibly-unreliable source into
-- the trusted one. Fully replaced on each live extraction run.
CREATE TABLE IF NOT EXISTS salesforce_data_auto (
  id INTEGER PRIMARY KEY,
  advisor_name TEXT NOT NULL,
  advisor_number TEXT,
  task_subtype TEXT,
  subject TEXT,
  comments TEXT,
  interaction_type TEXT,
  completed_date_time TEXT,
  district_vp_wholesaling TEXT,
  pwm TEXT,
  book_size TEXT,
  assets_under_management TEXT,
  new_business_ytd TEXT,
  created_date TEXT,
  start_date TEXT,
  status TEXT,
  area TEXT,
  region_office_number TEXT,
  assigned TEXT,
  raw_payload TEXT,
  ingested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS salesforce_data_auto_advisor_name_idx
  ON salesforce_data_auto (advisor_name);

-- One row per Tableau export line. Deduplicated on content_hash (a SHA-256 of
-- the data fields computed in ingest.py) via INSERT ... ON CONFLICT, so
-- re-uploading the same export is a no-op for rows that already exist.
CREATE TABLE IF NOT EXISTS tableau_data (
  id INTEGER PRIMARY KEY,
  advisor_name TEXT NOT NULL,
  advisor_name_number TEXT,
  segment TEXT,
  date TEXT,
  area_name TEXT,
  region TEXT,
  measure_names TEXT,
  account_count_fund_formatted REAL,
  client_count_fund_formatted REAL,
  fund_formatted TEXT,
  approved_to_buy TEXT,
  area TEXT,
  division_manager TEXT,
  fund_family TEXT,
  investment_vehicle TEXT,
  pwm TEXT,
  region_name TEXT,
  measure_values REAL,
  raw_payload TEXT,
  content_hash TEXT,
  ingested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS tableau_data_advisor_name_idx
  ON tableau_data (advisor_name);

CREATE UNIQUE INDEX IF NOT EXISTS tableau_data_content_hash_idx
  ON tableau_data (content_hash);

-- Legacy flat mirror of the consultant scorecard. Not read by the meeting
-- prep flow (superseded by consultant_scorecard_monthly/_metric below); kept
-- only because scripts/check_sqlite_tables.py and the original ingest path
-- still populate it, matching pre-migration behavior exactly.
CREATE TABLE IF NOT EXISTS consultant_scorecard_data (
  id INTEGER PRIMARY KEY,
  advisor_name TEXT NOT NULL,
  advisor_number TEXT,
  area TEXT,
  ro_number TEXT,
  region TEXT,
  division TEXT,
  base_achievement_level TEXT,
  etf_completed_and_approved TEXT,
  designation TEXT,
  sales_start_date TEXT,
  termination_date TEXT,
  tenure_category TEXT,
  pwm_indicator TEXT,
  dealer_code TEXT,
  insurance_expiry_date TEXT,
  key_driver_score TEXT,
  client_bp_count TEXT,
  assets_under_management TEXT,
  third_party_assets TEXT,
  assets_under_administration TEXT,
  raw_payload TEXT,
  ingested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS consultant_scorecard_data_advisor_name_idx
  ON consultant_scorecard_data (advisor_name);

CREATE INDEX IF NOT EXISTS consultant_scorecard_data_advisor_number_idx
  ON consultant_scorecard_data (advisor_number);

-- Audit copy of every consultant scorecard source row (one per advisor line
-- in the workbook). Deduplicated on content_hash, same pattern as tableau_data.
CREATE TABLE IF NOT EXISTS consultant_scorecard_raw (
  id INTEGER PRIMARY KEY,
  source_file TEXT NOT NULL,
  sheet_name TEXT NOT NULL,
  report_date TEXT NOT NULL,
  source_row_number INTEGER NOT NULL,
  advisor_number TEXT,
  advisor_name TEXT,
  raw_payload TEXT NOT NULL,
  content_hash TEXT,
  ingested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS consultant_scorecard_raw_report_date_idx
  ON consultant_scorecard_raw (report_date);

CREATE INDEX IF NOT EXISTS consultant_scorecard_raw_advisor_number_idx
  ON consultant_scorecard_raw (advisor_number);

CREATE UNIQUE INDEX IF NOT EXISTS consultant_scorecard_raw_content_hash_idx
  ON consultant_scorecard_raw (content_hash);

-- One canonical row per (report_date, advisor_number); ingest.py upserts on
-- that natural key so re-ingesting the same report never duplicates rows.
CREATE TABLE IF NOT EXISTS consultant_scorecard_monthly (
  id INTEGER PRIMARY KEY,
  source_file TEXT NOT NULL,
  report_date TEXT NOT NULL,
  advisor_number TEXT NOT NULL,
  advisor_name TEXT,
  area TEXT,
  ro_number INTEGER,
  region TEXT,
  division INTEGER,
  base_achievement_level TEXT,
  etf_completed_approved INTEGER,
  designation TEXT,
  sales_start_date TEXT,
  termination_date TEXT,
  tenure_category TEXT,
  pwm_indicator INTEGER,
  dealer_code TEXT,
  insurance_expiry_date TEXT,
  key_driver_score REAL,
  raw_payload TEXT,
  ingested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  UNIQUE (report_date, advisor_number)
);

CREATE INDEX IF NOT EXISTS consultant_scorecard_monthly_advisor_name_idx
  ON consultant_scorecard_monthly (advisor_name);

CREATE INDEX IF NOT EXISTS consultant_scorecard_monthly_advisor_number_idx
  ON consultant_scorecard_monthly (advisor_number);

-- Individual metrics for a scorecard_monthly row. Re-derived (delete + insert
-- for the touched scorecard_ids) on every ingest rather than upserted, since
-- metrics have no natural unique key of their own -- see ingest.py.
CREATE TABLE IF NOT EXISTS consultant_scorecard_metric (
  id INTEGER PRIMARY KEY,
  scorecard_id INTEGER NOT NULL REFERENCES consultant_scorecard_monthly (id) ON DELETE CASCADE,
  source_column TEXT NOT NULL,
  metric_group TEXT NOT NULL,
  metric_period TEXT,
  metric_name TEXT NOT NULL,
  value_numeric REAL,
  value_text TEXT,
  value_date TEXT,
  unit TEXT,
  french_label TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS consultant_scorecard_metric_scorecard_id_idx
  ON consultant_scorecard_metric (scorecard_id);

CREATE INDEX IF NOT EXISTS consultant_scorecard_metric_group_period_idx
  ON consultant_scorecard_metric (metric_group, metric_period, metric_name);

-- Audit log for uploads made through the /upload UI.
CREATE TABLE IF NOT EXISTS upload_batches (
  id INTEGER PRIMARY KEY,
  source_type TEXT NOT NULL CHECK (source_type IN ('tableau', 'consultant_scorecard')),
  file_name TEXT NOT NULL,
  rows_parsed INTEGER NOT NULL DEFAULT 0,
  rows_inserted INTEGER NOT NULL DEFAULT 0,
  rows_skipped_duplicate INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'success' CHECK (status IN ('success', 'error')),
  error_message TEXT,
  uploaded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS upload_batches_uploaded_at_idx
  ON upload_batches (uploaded_at DESC);

-- Audit log of every generated meeting-prep document (prompt + response).
-- Populated only when STORE_LLM_AUDIT_CONTENT=true (default true, matching
-- pre-migration behavior) -- see webapp/api.py.
CREATE TABLE IF NOT EXISTS meeting_prep_documents (
  id INTEGER PRIMARY KEY,
  advisor_name TEXT NOT NULL,
  prompt TEXT NOT NULL,
  response TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS meeting_prep_documents_advisor_name_idx
  ON meeting_prep_documents (advisor_name);
