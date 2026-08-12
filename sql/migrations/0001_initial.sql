-- Migration 0001: initial schema.
--
-- Applied and tracked by scripts/init_sqlite.py / db.py's migration runner.
-- Mirrors sql/schema.sql (the standalone reference copy of the same DDL) --
-- the schema_migrations table itself is bootstrapped separately by the
-- runner before any migration file is applied, so it is intentionally not
-- created here. Idempotent (IF NOT EXISTS everywhere): safe to re-run.

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

CREATE TABLE IF NOT EXISTS meeting_prep_documents (
  id INTEGER PRIMARY KEY,
  advisor_name TEXT NOT NULL,
  prompt TEXT NOT NULL,
  response TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS meeting_prep_documents_advisor_name_idx
  ON meeting_prep_documents (advisor_name);
