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

create table if not exists public.meeting_prep_documents (
  id bigint generated always as identity primary key,
  advisor_name text not null,
  prompt text not null,
  response text not null,
  created_at timestamptz not null default now()
);
