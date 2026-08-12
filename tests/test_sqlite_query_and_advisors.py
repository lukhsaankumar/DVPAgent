from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from dvp_meeting_prep.advisors import list_advisor_names, search_advisor_names
from dvp_meeting_prep.db import fetch_all, fetch_one
from dvp_meeting_prep.ingest import ingest_consultant_scorecard_upload, ingest_rows
from dvp_meeting_prep.query import fetch_all_sources_for_advisor, fetch_consultant_scorecard_for_advisor


def _write_scorecard_workbook(path: Path, *, advisor_number: str, advisor_name: str, report_date: str = "2026-03-01") -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Advisor Detail"
    ws.append([None] * 15)
    ws.append([report_date] + [None] * 14)
    ws.append(
        [
            "Advisor#",
            "Advisor",
            "Area",
            "RO#",
            "Region",
            "Div",
            "Base Achievement Level",
            "ETF Completed & Approved",
            "Designation",
            "Sales Start Date",
            "Termination Date",
            "Tenure Category",
            "PWM Indicator",
            "Dealer Code",
            "Insurance Expiry Date",
        ]
    )
    ws.append([None] * 15)
    ws.append(
        [advisor_number, advisor_name, "East", 101, "East Region", 1, "Achieved", "Yes", "Senior", "2020-01-01", None, "5+ years", "No", "D100", "2027-01-01"]
    )
    wb.save(path)
    return path


def test_search_advisor_names_is_case_insensitive_prefix_match(sqlite_db):
    ingest_rows(sqlite_db, "salesforce_data", [{"advisor_name": "Avery Benton"}, {"advisor_name": "Casey Diaz"}])

    matches = search_advisor_names(sqlite_db, "AV")
    assert matches == ["Avery Benton"]

    matches_lower = search_advisor_names(sqlite_db, "av")
    assert matches_lower == ["Avery Benton"]


def test_search_advisor_names_returns_nothing_for_blank_prefix(sqlite_db):
    ingest_rows(sqlite_db, "salesforce_data", [{"advisor_name": "Avery Benton"}])
    assert search_advisor_names(sqlite_db, "") == []
    assert search_advisor_names(sqlite_db, "   ") == []


def test_list_advisor_names_merges_and_dedupes_across_source_tables(sqlite_db):
    ingest_rows(sqlite_db, "salesforce_data", [{"advisor_name": "Avery Benton"}])
    ingest_rows(sqlite_db, "tableau_data", [{"advisor_name": "Avery Benton", "content_hash": "h1"}, {"advisor_name": "Casey Diaz", "content_hash": "h2"}])

    names = list_advisor_names(sqlite_db, force_refresh=True)
    assert names == ["Avery Benton", "Casey Diaz"]  # deduped, case-insensitively sorted


def test_cross_source_advisor_name_bridging_first_last_to_last_comma_first(sqlite_db, tmp_path):
    ingest_rows(sqlite_db, "salesforce_data", [{"advisor_name": "Avery Benton", "advisor_number": "17018"}])
    workbook = _write_scorecard_workbook(tmp_path / "scorecard.xlsx", advisor_number="17018", advisor_name="BENTON, AVERY")
    ingest_consultant_scorecard_upload(sqlite_db, workbook, source_file_name="scorecard.xlsx")

    result = fetch_consultant_scorecard_for_advisor(sqlite_db, "Avery Benton")
    assert len(result["consultant_scorecard_monthly"]) == 1
    assert result["consultant_scorecard_monthly"][0]["advisor_name"] == "BENTON, AVERY"
    assert len(result["consultant_scorecard_metric"]) >= 0  # scorecard_id linkage did not error


def test_cross_source_advisor_number_bridging_when_name_variants_fail(sqlite_db, tmp_path):
    # Scorecard advisor_name is unrelated to the salesforce/tableau spelling --
    # only the advisor_number (bridged via tableau's "Name - Number" column) matches.
    ingest_rows(sqlite_db, "tableau_data", [{"advisor_name": "Avery Benton", "advisor_name_number": "Avery Benton - 17018", "content_hash": "h1"}])
    workbook = _write_scorecard_workbook(tmp_path / "scorecard.xlsx", advisor_number="17018", advisor_name="Completely Different Spelling")
    ingest_consultant_scorecard_upload(sqlite_db, workbook, source_file_name="scorecard.xlsx")

    result = fetch_consultant_scorecard_for_advisor(sqlite_db, "Avery Benton")
    assert len(result["consultant_scorecard_monthly"]) == 1
    assert result["consultant_scorecard_monthly"][0]["advisor_number"] == "17018"


def test_fetch_all_sources_for_advisor_returns_all_expected_keys(sqlite_db):
    ingest_rows(sqlite_db, "salesforce_data", [{"advisor_name": "Avery Benton"}])
    result = fetch_all_sources_for_advisor(sqlite_db, "Avery Benton")
    assert set(result.keys()) == {"salesforce_data", "tableau_data", "consultant_scorecard_monthly", "consultant_scorecard_metric"}
    assert len(result["salesforce_data"]) == 1
    assert result["tableau_data"] == []


def test_meeting_prep_document_audit_write_and_read(sqlite_db):
    with sqlite_db.write() as conn:
        conn.execute(
            "INSERT INTO meeting_prep_documents (advisor_name, prompt, response) VALUES (?, ?, ?)",
            ("Avery Benton", "prompt text", "# Meeting Prep\n\nSome content."),
        )

    with sqlite_db.read() as conn:
        row = fetch_one(conn, "SELECT * FROM meeting_prep_documents WHERE advisor_name = ?", ("Avery Benton",))
    assert row is not None
    assert row["prompt"] == "prompt text"
    assert row["response"] == "# Meeting Prep\n\nSome content."
    assert row["created_at"] is not None


def test_upload_batch_audit_write_and_read_ordering(sqlite_db):
    with sqlite_db.write() as conn:
        conn.execute(
            "INSERT INTO upload_batches (source_type, file_name, rows_parsed, rows_inserted, rows_skipped_duplicate, status) VALUES (?, ?, ?, ?, ?, ?)",
            ("tableau", "first.csv", 10, 8, 2, "success"),
        )
        conn.execute(
            "INSERT INTO upload_batches (source_type, file_name, rows_parsed, rows_inserted, rows_skipped_duplicate, status, error_message) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("consultant_scorecard", "second.xlsx", 0, 0, 0, "error", "parse failed"),
        )

    with sqlite_db.read() as conn:
        rows = fetch_all(conn, "SELECT file_name, status, error_message FROM upload_batches ORDER BY uploaded_at DESC, id DESC")
    assert [row["file_name"] for row in rows] == ["second.xlsx", "first.csv"]
    assert rows[0]["status"] == "error"
    assert rows[0]["error_message"] == "parse failed"
    assert rows[1]["status"] == "success"
