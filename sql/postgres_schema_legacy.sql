-- ARCHIVED / UNUSED. This is the original Postgres (Supabase) schema, kept
-- only for historical reference. The application now runs on SQLite -- see
-- sql/schema.sql for the active schema and docs/TESTING_GEMINI_SQLITE_MIGRATION.md
-- for the migration this file is a record of. Nothing in the codebase reads
-- this file; it is not applied by any script.
create table if not exists public.salesforce_data (
  id bigint generated always as identity primary key,
  advisor_name text not null,
  advisor_number text,
  task_subtype text,
  subject text,
  comments text,
  interaction_type text,
  completed_date_time text,
  district_vp_wholesaling text,
  pwm text,
  book_size text,
  assets_under_management text,
  new_business_ytd text,
  created_date text,
  start_date text,
  status text,
  area text,
  region_office_number text,
  assigned text,
  raw_payload jsonb,
  ingested_at timestamptz not null default now()
);

create index if not exists salesforce_data_advisor_name_idx
  on public.salesforce_data (advisor_name);

create table if not exists public.tableau_data (
  id bigint generated always as identity primary key,
  advisor_name text not null,
  advisor_name_number text,
  segment text,
  date text,
  area_name text,
  region text,
  measure_names text,
  account_count_fund_formatted numeric,
  client_count_fund_formatted numeric,
  fund_formatted text,
  approved_to_buy text,
  area text,
  division_manager text,
  fund_family text,
  investment_vehicle text,
  pwm text,
  region_name text,
  measure_values numeric,
  raw_payload jsonb,
  ingested_at timestamptz not null default now()
);

create index if not exists tableau_data_advisor_name_idx
  on public.tableau_data (advisor_name);

create table if not exists public.consultant_scorecard_data (
  id bigint generated always as identity primary key,
  advisor_name text not null,
  advisor_number text,
  area text,
  ro_number text,
  region text,
  division text,
  base_achievement_level text,
  etf_completed_and_approved text,
  designation text,
  sales_start_date text,
  termination_date text,
  tenure_category text,
  pwm_indicator text,
  dealer_code text,
  insurance_expiry_date text,
  key_driver_score text,
  client_bp_count text,
  assets_under_management text,
  third_party_assets text,
  assets_under_administration text,
  raw_payload jsonb,
  ingested_at timestamptz not null default now()
);

create index if not exists consultant_scorecard_data_advisor_name_idx
  on public.consultant_scorecard_data (advisor_name);

create or replace view public.consultant_scorecard_metrics_v as
select
  c.id as consultant_row_id,
  c.advisor_name,
  c.advisor_number,
  c.area,
  c.region,
  m.key as metric_key,
  m.value as metric_value_text,
  nullif(regexp_replace(m.value, '[^0-9+\-.]', '', 'g'), '')::numeric as metric_value_numeric,
  c.ingested_at
from public.consultant_scorecard_data c
cross join lateral jsonb_each_text(coalesce(c.raw_payload -> 'metrics', '{}'::jsonb)) m;

create index if not exists consultant_scorecard_data_advisor_number_idx
  on public.consultant_scorecard_data (advisor_number);

create table if not exists public.consultant_scorecard_raw (
  id bigint generated always as identity primary key,
  source_file text not null,
  sheet_name text not null,
  report_date date not null,
  source_row_number integer not null,
  advisor_number text,
  advisor_name text,
  raw_payload jsonb not null,
  ingested_at timestamptz not null default now()
);

create index if not exists consultant_scorecard_raw_report_date_idx
  on public.consultant_scorecard_raw (report_date);

create index if not exists consultant_scorecard_raw_advisor_number_idx
  on public.consultant_scorecard_raw (advisor_number);

create table if not exists public.consultant_scorecard_monthly (
  id bigint generated always as identity primary key,
  source_file text not null,
  report_date date not null,
  advisor_number text not null,
  advisor_name text,
  area text,
  ro_number integer,
  region text,
  division integer,
  base_achievement_level text,
  etf_completed_approved boolean,
  designation text,
  sales_start_date date,
  termination_date date,
  tenure_category text,
  pwm_indicator boolean,
  dealer_code text,
  insurance_expiry_date date,
  key_driver_score numeric,
  raw_payload jsonb,
  ingested_at timestamptz not null default now(),
  unique (report_date, advisor_number)
);

create index if not exists consultant_scorecard_monthly_advisor_name_idx
  on public.consultant_scorecard_monthly (advisor_name);

create index if not exists consultant_scorecard_monthly_advisor_number_idx
  on public.consultant_scorecard_monthly (advisor_number);

create table if not exists public.consultant_scorecard_metric (
  id bigint generated always as identity primary key,
  scorecard_id bigint not null references public.consultant_scorecard_monthly(id) on delete cascade,
  source_column text not null,
  metric_group text not null,
  metric_period text,
  metric_name text not null,
  value_numeric numeric,
  value_text text,
  value_date date,
  unit text,
  french_label text,
  created_at timestamptz not null default now()
);

create index if not exists consultant_scorecard_metric_scorecard_id_idx
  on public.consultant_scorecard_metric (scorecard_id);

create index if not exists consultant_scorecard_metric_group_period_idx
  on public.consultant_scorecard_metric (metric_group, metric_period, metric_name);

create table if not exists public.meeting_prep_documents (
  id bigint generated always as identity primary key,
  advisor_name text not null,
  prompt text not null,
  response text not null,
  created_at timestamptz not null default now()
);

create index if not exists meeting_prep_documents_advisor_name_idx
  on public.meeting_prep_documents (advisor_name);

-- Dedup support for UI-driven uploads: each row's content is hashed so a
-- re-uploaded file (or overlapping export) upserts instead of duplicating.
alter table public.tableau_data
  add column if not exists content_hash text;

create unique index if not exists tableau_data_content_hash_idx
  on public.tableau_data (content_hash);

alter table public.consultant_scorecard_raw
  add column if not exists content_hash text;

create unique index if not exists consultant_scorecard_raw_content_hash_idx
  on public.consultant_scorecard_raw (content_hash);

-- Audit log for uploads made through the /upload UI.
create table if not exists public.upload_batches (
  id bigint generated always as identity primary key,
  source_type text not null check (source_type in ('tableau', 'consultant_scorecard')),
  file_name text not null,
  rows_parsed integer not null default 0,
  rows_inserted integer not null default 0,
  rows_skipped_duplicate integer not null default 0,
  status text not null default 'success' check (status in ('success', 'error')),
  error_message text,
  uploaded_at timestamptz not null default now()
);

create index if not exists upload_batches_uploaded_at_idx
  on public.upload_batches (uploaded_at desc);
