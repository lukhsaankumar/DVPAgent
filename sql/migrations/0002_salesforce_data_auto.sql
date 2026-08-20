-- Migration 0002: add salesforce_data_auto.
--
-- Splits live Salesforce extraction data out of salesforce_data (which is
-- now the "legacy"/manual-spreadsheet-only table -- see sql/schema.sql's
-- comments on both tables for why). Purely additive: never touches existing
-- rows in any table.

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
