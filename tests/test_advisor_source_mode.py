from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

from dvp_meeting_prep.advisors import list_advisor_names, search_advisor_names
from dvp_meeting_prep.db import SALESFORCE_TABLE_BY_ADVISOR_SOURCE_MODE
from dvp_meeting_prep.ingest import ingest_all_sources, ingest_rows
from dvp_meeting_prep.query import fetch_all_sources_for_advisor


def _write_minimal_scorecard_workbook(path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Advisor Detail"
    ws.append([None] * 15)
    ws.append(["2026-03-01"] + [None] * 14)
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
    ws.append(["99999", "SCORECARD, ONLY", "East", 1, "East Region", 1, "Achieved", "Yes", "Senior", "2020-01-01", None, "5+ years", "No", "D1", "2027-01-01"])
    wb.save(path)
    return path


def _write_minimal_tableau_csv(path: Path, advisor_name: str) -> Path:
    header = (
        "Advisor,Advisor Name - Number,Segment,Date,Area Name,Region,Measure Names,"
        "Account Count Fund Formatted,Client Count Fund Formatted,Fund Formatted,"
        "Approved to Buy,Area,Division Manager,Fund Family,Investment Vehicle,PWM,"
        "Region Name,Measure Values\n"
    )
    row = f"{advisor_name},{advisor_name} - 99998,Retail,01/15/2026,East,East Region,Sales,5,5,Fund A,Yes,East,Dana Manager,Family A,Mutual Fund,No,East Region,5"
    path.write_text(header + row + "\n", encoding="utf-8")
    return path


def test_ingest_all_sources_routes_csv_to_legacy_table(sqlite_db, base_env, tmp_path):
    base_env.setenv("DATA_SOURCE", "csv")
    scorecard_path = _write_minimal_scorecard_workbook(tmp_path / "scorecard.xlsx")
    tableau_path = _write_minimal_tableau_csv(tmp_path / "tableau.csv", "Csv Mode Advisor")

    with patch("dvp_meeting_prep.data_source.read_salesforce_rows") as mock_read:
        mock_read.return_value = [{"advisor_name": "Legacy Advisor"}]
        counts = ingest_all_sources(sqlite_db, "unused.xlsx", str(tableau_path), str(scorecard_path))

    assert "salesforce_data" in counts
    assert "salesforce_data_auto" not in counts
    with sqlite_db.read() as conn:
        legacy_names = [r["advisor_name"] for r in conn.execute("SELECT advisor_name FROM salesforce_data").fetchall()]
        auto_count = conn.execute("SELECT COUNT(*) FROM salesforce_data_auto").fetchone()[0]
    assert legacy_names == ["Legacy Advisor"]
    assert auto_count == 0


def test_ingest_all_sources_routes_live_salesforce_to_auto_table(sqlite_db, base_env, tmp_path):
    base_env.setenv("DATA_SOURCE", "salesforce")
    scorecard_path = _write_minimal_scorecard_workbook(tmp_path / "scorecard.xlsx")
    tableau_path = _write_minimal_tableau_csv(tmp_path / "tableau.csv", "Auto Mode Advisor")

    fake_result = type("FakeResult", (), {"legacy_rows": [{"advisor_name": "Auto Advisor"}]})()
    with patch("dvp_meeting_prep.salesforce.extraction.run_extraction") as mock_run:
        mock_run.return_value = fake_result
        counts = ingest_all_sources(sqlite_db, None, str(tableau_path), str(scorecard_path))

    assert "salesforce_data_auto" in counts
    assert "salesforce_data" not in counts
    with sqlite_db.read() as conn:
        auto_names = [r["advisor_name"] for r in conn.execute("SELECT advisor_name FROM salesforce_data_auto").fetchall()]
        legacy_count = conn.execute("SELECT COUNT(*) FROM salesforce_data").fetchone()[0]
    assert auto_names == ["Auto Advisor"]
    assert legacy_count == 0


def _seed_two_modes(sqlite_db):
    ingest_rows(sqlite_db, "salesforce_data", [{"advisor_name": "Legacy Only Advisor"}])
    ingest_rows(sqlite_db, "salesforce_data_auto", [{"advisor_name": "Auto Only Advisor"}])
    ingest_rows(sqlite_db, "tableau_data", [{"advisor_name": "Tableau Only Advisor", "content_hash": "h1"}])


def test_dropdown_shows_only_legacy_advisors_in_legacy_mode(sqlite_db):
    _seed_two_modes(sqlite_db)
    names = list_advisor_names(sqlite_db, salesforce_table="salesforce_data", force_refresh=True)
    assert "Legacy Only Advisor" in names
    assert "Auto Only Advisor" not in names
    assert "Tableau Only Advisor" in names  # tableau-only advisors show up regardless of mode


def test_dropdown_shows_only_auto_advisors_in_auto_mode(sqlite_db):
    _seed_two_modes(sqlite_db)
    names = list_advisor_names(sqlite_db, salesforce_table="salesforce_data_auto", force_refresh=True)
    assert "Auto Only Advisor" in names
    assert "Legacy Only Advisor" not in names
    assert "Tableau Only Advisor" in names


def test_dropdown_shows_scorecard_only_advisor_regardless_of_mode(sqlite_db, tmp_path):
    from dvp_meeting_prep.ingest import ingest_consultant_scorecard_upload

    workbook = _write_minimal_scorecard_workbook(tmp_path / "scorecard.xlsx")
    ingest_consultant_scorecard_upload(sqlite_db, workbook, source_file_name="scorecard.xlsx")

    legacy_names = list_advisor_names(sqlite_db, salesforce_table="salesforce_data", force_refresh=True)
    auto_names = list_advisor_names(sqlite_db, salesforce_table="salesforce_data_auto", force_refresh=True)
    assert "SCORECARD, ONLY" in legacy_names
    assert "SCORECARD, ONLY" in auto_names


def test_search_advisor_names_respects_mode(sqlite_db):
    _seed_two_modes(sqlite_db)
    legacy_matches = search_advisor_names(sqlite_db, "Legacy", salesforce_table="salesforce_data")
    auto_matches = search_advisor_names(sqlite_db, "Legacy", salesforce_table="salesforce_data_auto")
    assert legacy_matches == ["Legacy Only Advisor"]
    assert auto_matches == []


def test_fetch_all_sources_for_advisor_uses_the_correct_table_per_mode(sqlite_db):
    ingest_rows(sqlite_db, "salesforce_data", [{"advisor_name": "Shared Name", "comments": "from legacy"}])
    ingest_rows(sqlite_db, "salesforce_data_auto", [{"advisor_name": "Shared Name", "comments": "from auto"}])

    legacy_result = fetch_all_sources_for_advisor(sqlite_db, "Shared Name", salesforce_table="salesforce_data")
    auto_result = fetch_all_sources_for_advisor(sqlite_db, "Shared Name", salesforce_table="salesforce_data_auto")

    # Stable "salesforce_data" key regardless of which physical table backed it.
    assert legacy_result["salesforce_data"][0]["comments"] == "from legacy"
    assert auto_result["salesforce_data"][0]["comments"] == "from auto"


def test_salesforce_table_by_advisor_source_mode_covers_both_modes():
    assert SALESFORCE_TABLE_BY_ADVISOR_SOURCE_MODE == {
        "legacy": "salesforce_data",
        "auto": "salesforce_data_auto",
    }
