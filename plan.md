# DVP Meeting Prep Agent Plan

> **Historical document.** This was the original MVP plan and describes the
> initial Supabase + OpenAI architecture. The app has since migrated to
> local SQLite (see `sql/schema.sql`, `src/dvp_meeting_prep/db.py`) and
> Gemini Enterprise via Google ADC (see `src/dvp_meeting_prep/llm.py`). Kept
> for historical context only -- see `README.md` for the current
> architecture.

## Goal
Build an internal agent that prepares advisors for DVP meetings by pulling data from Salesforce, Tableau, and the consultant scorecard, loading each source into its own Supabase table, and generating a meeting prep document with an LLM.

## MVP Goals

1. Support advisor data ingestion from three MVP sources:
   - Salesforce lookup data stored in a text file for now.
   - Tableau dummy CSV data stored in a text file for now.
   - Consultant scorecard spreadsheet stored in a `data/` folder.
2. Create one Supabase table per source and load the source rows into those tables.
3. Preserve the raw source structure so the first POC can query exactly what was ingested.
4. Query the database at runtime by exact advisor name from a Python terminal script.
5. Print the full query result from each source table for that advisor.
6. Pass the combined source results into an OpenAI model prompt to generate the meeting prep document.
7. Keep the POC small and script-driven rather than building a full UI first.

## Recommended MVP Scope

Start with a narrow proof of concept before automating everything.

- Use Supabase as the first database layer.
- Use text-file inputs for Salesforce lookup data and Tableau dummy CSV data in the MVP.
- Keep the consultant scorecard as a spreadsheet in the `data/` folder.
- Load each source into a dedicated table with a schema that matches the source file structure.
- Use a Python terminal script to query by exact advisor name.
- Print the raw query output from each source table before sending anything to the LLM.
- Generate one structured meeting prep document format.
- Keep manual override support for identity matching later, not in the first POC.

## High-Level Architecture

- Data source files: Salesforce text file, Tableau dummy CSV, consultant scorecard spreadsheet.
- Ingestion script: parses each source and inserts rows into one Supabase table per source.
- Supabase Postgres: stores the three source tables plus an optional meeting prep output table.
- Python lookup script: accepts an exact advisor name, queries each table, and prints the results.
- OpenAI prompt script: combines the query results and sends them to the LLM.
- Output: generated meeting prep document for the advisor.

## Core Data Flow

1. User provides three source inputs: Salesforce text file, Tableau dummy CSV, and consultant scorecard spreadsheet.
2. A Python ingestion script parses each source file.
3. Each parsed source is inserted into its own Supabase table.
4. The schema for each table is chosen to match the source structure as closely as possible for the POC.
5. A Python lookup script accepts an exact advisor name.
6. The script queries the Salesforce table, Tableau table, and consultant scorecard table for that advisor.
7. The script prints the full query result from each table.
8. The combined results are passed into an OpenAI prompt.
9. The LLM returns the meeting prep document.
10. The generated output is saved or printed for review.

## Database Entities

- SalesforceData: one row per Salesforce source record.
- TableauData: one row per Tableau dummy CSV row.
- ConsultantScorecardData: one row per consultant scorecard record.
- MeetingPrepDocument: generated LLM output.

For the first POC, keep the schema source-shaped instead of over-normalizing. A canonical advisor table can be added later if matching becomes necessary.

### Tableau CSV Shape For The POC

The Tableau sample file contains these columns and should be treated as the starting schema for the `tableau_data` table:

- Segment
- Date
- Area Name
- Region
- Advisor
- Measure Names
- Account Count Fund Formatted
- Client Count Fund Formatted
- Fund Formatted
- Advisor Name - Number
- Approved to Buy
- Area
- Division Manager
- Fund Family
- Investment Vehicle
- PWM
- Region Name
- Measure Values

Suggested types:

- Text fields for names, labels, flags, and categories.
- Date for the `Date` column.
- Integer or numeric for the count and value fields.
- Boolean or text for approval-style values if the CSV uses `Y` and `N`.

## MVP Delivery Steps

### Phase 1: Foundation

1. Create the Supabase project and connect the Postgres database.
2. Define the source tables for Salesforce, Tableau, and consultant scorecard data.
3. Add the SQL DDL as the primary way to create and update the tables.
4. Create a lightweight Python project structure for ingestion, lookup, and prompt generation.

### Phase 2: Ingestion

1. Parse the Salesforce text file.
2. Parse the Tableau dummy CSV.
3. Parse the consultant scorecard spreadsheet from the `data/` folder.
4. Insert all parsed rows into the matching Supabase table.
5. Verify row counts and sample records after each load.

### Phase 3: Matching and Normalization

1. For the POC, use exact advisor name matching only.
2. Query each source table independently with the exact advisor name.
3. Keep normalization minimal until the source ingestion works reliably.
4. Add canonical advisor matching later if the POC needs it.

### Phase 4: Meeting Prep Generation

1. Build a Python script that takes an advisor name from the terminal.
2. Query the three Supabase tables for that exact advisor.
3. Print the full query results from each table.
4. Build a prompt from those results.
5. Send the prompt to the OpenAI API.
6. Print or save the meeting prep response.

### Phase 5: Operationalization

1. Add ingestion refresh scripts or scheduled jobs later.
2. Add job status tracking and error reporting later.
3. Add audit logs and document versioning later.
4. Add access control and data protection for sensitive advisor information later.

## Supabase Table Creation

Use SQL as the main way to create and update the tables.

```sql
create table if not exists public.salesforce_data (
  id bigint generated always as identity primary key,
  advisor_name text not null,
  source_key text,
  source_value text,
  raw_payload jsonb,
  ingested_at timestamptz not null default now()
);

create index if not exists salesforce_data_advisor_name_idx
  on public.salesforce_data (advisor_name);

create table if not exists public.tableau_data (
  id bigint generated always as identity primary key,
  segment text,
  date date,
  area_name text,
  region text,
  advisor text not null,
  measure_names text,
  account_count_fund_formatted integer,
  client_count_fund_formatted integer,
  fund_formatted text,
  advisor_name_number text,
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

create index if not exists tableau_data_advisor_idx
  on public.tableau_data (advisor);

create table if not exists public.consultant_scorecard_data (
  id bigint generated always as identity primary key,
  advisor_name text not null,
  source_key text,
  source_value text,
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
```

## Exact POC Script Flow

1. Run the ingestion script for the three source files.
2. Confirm rows landed in `salesforce_data`, `tableau_data`, and `consultant_scorecard_data`.
3. Run a Python script with an exact advisor name.
4. The script queries each table separately for that advisor.
5. The script prints the three query result sets.
6. The script assembles the prompt from those three result sets.
7. The script sends the prompt to OpenAI.
8. The script prints or saves the returned meeting prep document.
9. Repeat for another advisor as a manual validation loop.

## Suggested Folder Structure

```text
dvp-meeting-prep/
  src/
    ingest/
      salesforce.py
      tableau.py
      consultant_scorecard.py
    lookup/
      advisor_lookup.py
    prompts/
      build_prompt.py
    llm/
      openai_client.py
  data/
    consultant_scorecard.xlsx
  lookups/
    salesforce.txt
    tableau_dummy.csv
  sql/
    schema.sql
  scripts/
    ingest_all.py
    query_advisor.py
    run_meeting_prep.py
  docs/
    plan.md
    prompts/
  tests/
    test_ingestion.py
    test_lookup.py
```

## Risks And Notes

- The Tableau CSV is wide and should probably be stored in source-shaped columns first instead of being overly normalized.
- Exact advisor-name matching is fine for the POC but will not be robust enough for production.
- Source files may have inconsistent column names or null values, so ingestion validation is still important.
- If the data is sensitive, encryption, access control, and prompt redaction should be part of the next phase.

## Success Criteria For The MVP

- The three source files can be ingested into Supabase tables.
- The Tableau dummy CSV schema is reflected in the `tableau_data` table.
- A Python terminal script can query one advisor by exact name.
- The script prints the full results from each source table.
- The combined results can be sent to OpenAI to generate a meeting prep document.
- The generated prep output can be reviewed before the meeting.

## Next Build Recommendation

Implement Supabase schema creation first, then ingestion scripts, then exact-name lookup, then the OpenAI prompt flow. That sequence keeps the POC small and directly aligned with the manual validation path.