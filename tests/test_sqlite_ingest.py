from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
import pytest

from dvp_meeting_prep.db import IntegrityError
from dvp_meeting_prep.ingest import (
    ingest_consultant_scorecard_upload,
    ingest_rows,
    ingest_tableau_upload,
)
from dvp_meeting_prep.query import fetch_rows_for_advisor

TABLEAU_HEADER = (
    "Advisor,Advisor Name - Number,Segment,Date,Area Name,Region,Measure Names,"
    "Account Count Fund Formatted,Client Count Fund Formatted,Fund Formatted,"
    "Approved to Buy,Area,Division Manager,Fund Family,Investment Vehicle,PWM,"
    "Region Name,Measure Values\n"
)


def _write_tableau_csv(path: Path, rows: list[str]) -> Path:
    path.write_text(TABLEAU_HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def _tableau_row(advisor: str = "Avery Benton", fund: str = "Fund A", count: str = "5") -> str:
    return (
        f"{advisor},{advisor} - 17018,Retail,01/15/2026,East,East Region,Sales,"
        f"{count},{count},{fund},Yes,East,Dana Manager,Family A,Mutual Fund,No,East Region,{count}"
    )


def _write_scorecard_workbook(
    path: Path, *, report_date: str = "2026-03-01", advisors: list[tuple[str, str]] | None = None
) -> Path:
    """Build a minimal .xlsx matching the shape parse_consultant_scorecard()
    expects: sheet titled 'Advisor Detail', 4 header rows, then data rows
    starting at row 5. advisors is a list of (advisor_number, advisor_name).
    """
    advisors = advisors or [("17018", "Avery Benton")]
    wb = Workbook()
    ws = wb.active
    ws.title = "Advisor Detail"

    ws.append([None] * 15)  # row 1: major group headers (unused here)
    ws.append([report_date] + [None] * 14)  # row 2: A2 holds the report date
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
    )  # row 3: english headers
    ws.append([None] * 15)  # row 4: french headers (unused here)

    for advisor_number, advisor_name in advisors:
        ws.append(
            [
                advisor_number,
                advisor_name,
                "East",
                101,
                "East Region",
                1,
                "Achieved",
                "Yes",
                "Senior",
                "2020-01-01",
                None,
                "5+ years",
                "No",
                "D100",
                "2027-01-01",
            ]
        )

    wb.save(path)
    return path


def test_ingest_rows_replace_existing_atomically_swaps_contents(sqlite_db):
    ingest_rows(sqlite_db, "salesforce_data", [{"advisor_name": "First Advisor"}])
    ingest_rows(sqlite_db, "salesforce_data", [{"advisor_name": "Second Advisor"}])

    with sqlite_db.read() as conn:
        names = [row["advisor_name"] for row in conn.execute("SELECT advisor_name FROM salesforce_data").fetchall()]
    assert names == ["Second Advisor"]


def test_atomic_replace_never_leaves_table_empty_after_failed_ingest(sqlite_db):
    ingest_rows(sqlite_db, "salesforce_data", [{"advisor_name": "Original Advisor"}])

    with pytest.raises(IntegrityError):
        # advisor_name is NOT NULL -- this row violates the constraint mid-replace.
        ingest_rows(sqlite_db, "salesforce_data", [{"advisor_name": None}])

    with sqlite_db.read() as conn:
        rows = conn.execute("SELECT advisor_name FROM salesforce_data").fetchall()
    assert [row["advisor_name"] for row in rows] == ["Original Advisor"]  # never left empty


def test_tableau_upload_dedups_within_and_across_batches(sqlite_db, tmp_path):
    csv_path = _write_tableau_csv(tmp_path / "tableau.csv", [_tableau_row(), _tableau_row()])  # exact duplicate

    first = ingest_tableau_upload(sqlite_db, csv_path)
    assert first == {"rows_parsed": 2, "rows_inserted": 1, "rows_skipped_duplicate": 1}

    second = ingest_tableau_upload(sqlite_db, csv_path)  # re-upload the same file
    assert second == {"rows_parsed": 2, "rows_inserted": 0, "rows_skipped_duplicate": 2}

    with sqlite_db.read() as conn:
        count = conn.execute("SELECT COUNT(*) FROM tableau_data").fetchone()[0]
    assert count == 1


def test_tableau_upload_json_and_advisor_lookup_round_trip(sqlite_db, tmp_path):
    csv_path = _write_tableau_csv(tmp_path / "tableau.csv", [_tableau_row(advisor="Avery Benton")])
    ingest_tableau_upload(sqlite_db, csv_path)

    rows = fetch_rows_for_advisor(sqlite_db, "tableau_data", "Avery Benton")
    assert len(rows) == 1
    assert isinstance(rows[0]["raw_payload"], dict)  # JSON TEXT column deserialized back to dict
    assert rows[0]["raw_payload"]["Advisor"] == "Avery Benton"


def test_consultant_scorecard_upsert_by_natural_key_avoids_duplicates(sqlite_db, tmp_path):
    workbook_path = _write_scorecard_workbook(tmp_path / "scorecard.xlsx")

    first = ingest_consultant_scorecard_upload(sqlite_db, workbook_path, source_file_name="scorecard.xlsx")
    assert first["consultant_scorecard_monthly_upserted"] == 1

    second = ingest_consultant_scorecard_upload(sqlite_db, workbook_path, source_file_name="scorecard.xlsx")
    assert second["consultant_scorecard_monthly_upserted"] == 1  # re-upserted, not duplicated

    with sqlite_db.read() as conn:
        monthly_count = conn.execute("SELECT COUNT(*) FROM consultant_scorecard_monthly").fetchone()[0]
    assert monthly_count == 1


def test_consultant_scorecard_raw_dedups_on_content_hash(sqlite_db, tmp_path):
    workbook_path = _write_scorecard_workbook(tmp_path / "scorecard.xlsx")

    first = ingest_consultant_scorecard_upload(sqlite_db, workbook_path, source_file_name="scorecard.xlsx")
    assert first["rows_inserted"] == 1
    assert first["rows_skipped_duplicate"] == 0

    second = ingest_consultant_scorecard_upload(sqlite_db, workbook_path, source_file_name="scorecard.xlsx")
    assert second["rows_inserted"] == 0
    assert second["rows_skipped_duplicate"] == 1

    with sqlite_db.read() as conn:
        raw_count = conn.execute("SELECT COUNT(*) FROM consultant_scorecard_raw").fetchone()[0]
    assert raw_count == 1


def test_consultant_scorecard_boolean_round_trip(sqlite_db, tmp_path):
    workbook_path = _write_scorecard_workbook(tmp_path / "scorecard.xlsx")
    ingest_consultant_scorecard_upload(sqlite_db, workbook_path, source_file_name="scorecard.xlsx")

    rows = fetch_rows_for_advisor(sqlite_db, "consultant_scorecard_monthly", "Avery Benton")
    assert len(rows) == 1
    assert rows[0]["etf_completed_approved"] is True  # stored as INTEGER 0/1, read back as bool
    assert rows[0]["pwm_indicator"] is False
