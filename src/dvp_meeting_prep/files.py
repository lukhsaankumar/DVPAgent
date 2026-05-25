from __future__ import annotations

from csv import DictReader
from datetime import datetime
from pathlib import Path
import json
import re
from typing import Any

from openpyxl import load_workbook


def _normalize_header(header: Any) -> str:
    if header is None:
        return ""
    text = str(header).strip().lower()
    text = text.replace("\n", " ")
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _clean_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    text = str(value).strip()
    return text if text != "" else None


def _to_number(value: Any) -> str | None:
    cleaned = _clean_value(value)
    if cleaned is None:
        return None
    return str(cleaned)


def _to_float(value: Any) -> float | None:
    cleaned = _clean_value(value)
    if cleaned is None:
        return None
    try:
        return float(str(cleaned).replace(",", ""))
    except ValueError:
        return None


def _to_int(value: Any) -> int | None:
    number = _to_float(value)
    if number is None:
        return None
    return int(number)


def _is_blank_row(row: list[Any]) -> bool:
    return all(_clean_value(value) is None for value in row)


def _find_header_row(rows: list[list[Any]], expected_fields: set[str]) -> tuple[int, list[Any]]:
    expected_normalized = {_normalize_header(value) for value in expected_fields}
    for index, row in enumerate(rows):
        normalized = {_normalize_header(value) for value in row if _clean_value(value) is not None}
        if expected_normalized.issubset(normalized):
            return index, row
    raise RuntimeError(f"Could not locate a header row containing {sorted(expected_normalized)}")


def _find_value_by_header(headers: list[Any], row: list[Any], target_header: str, *, exact: bool = False) -> Any:
    target = _normalize_header(target_header)
    for index, header in enumerate(headers):
        if exact:
            if _clean_value(header) is not None and str(header).strip().lower() == target_header.strip().lower():
                return _clean_value(row[index] if index < len(row) else None)
            continue
        if _normalize_header(header) == target:
            return _clean_value(row[index] if index < len(row) else None)
    return None


def _row_payload(headers: list[Any], row: list[Any]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for index, header in enumerate(headers):
        value = row[index] if index < len(row) else None
        if _clean_value(header) is None and _clean_value(value) is None:
            continue
        payload.append(
            {
                "column_index": index + 1,
                "header": _clean_value(header),
                "value": _clean_value(value),
            }
        )
    return payload


def read_salesforce_rows(path: str | Path) -> list[dict[str, Any]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = None
    for candidate in workbook.worksheets:
        if "ws team crm history" in candidate.title.lower():
            sheet = candidate
            break
    if sheet is None:
        raise RuntimeError("Could not find the Salesforce worksheet named 'WS Team CRM History'.")

    rows = list(sheet.iter_rows(values_only=True))
    header_index, headers = _find_header_row(rows, {"advisor_number", "name", "task_subtype"})

    parsed: list[dict[str, Any]] = []
    for row in rows[header_index + 1 :]:
        if _is_blank_row(list(row)):
            continue

        raw_payload = {str(headers[i]): _clean_value(row[i] if i < len(row) else None) for i in range(len(headers))}
        advisor_name = _find_value_by_header(headers, list(row), "Name")
        if advisor_name is None:
            continue

        parsed.append(
            {
                "advisor_name": advisor_name,
                "advisor_number": _to_number(_find_value_by_header(headers, list(row), "Advisor Number")),
                "task_subtype": _find_value_by_header(headers, list(row), "Task Subtype"),
                "subject": _find_value_by_header(headers, list(row), "Subject"),
                "comments": _find_value_by_header(headers, list(row), "Comments"),
                "interaction_type": _find_value_by_header(headers, list(row), "Interaction Type"),
                "completed_date_time": _find_value_by_header(headers, list(row), "Completed Date/Time"),
                "district_vp_wholesaling": _find_value_by_header(headers, list(row), "District VP (Wholesaling)"),
                "pwm": _find_value_by_header(headers, list(row), "PWM"),
                "book_size": _find_value_by_header(headers, list(row), "Book Size"),
                "assets_under_management": _find_value_by_header(headers, list(row), "Assets Under Management (AUM)"),
                "new_business_ytd": _find_value_by_header(headers, list(row), "New Business YTD"),
                "created_date": _find_value_by_header(headers, list(row), "Created Date"),
                "start_date": _find_value_by_header(headers, list(row), "Start Date"),
                "status": _find_value_by_header(headers, list(row), "Status"),
                "area": _find_value_by_header(headers, list(row), "Area"),
                "region_office_number": _find_value_by_header(headers, list(row), "Region Office Number"),
                "assigned": _find_value_by_header(headers, list(row), "Assigned"),
                "raw_payload": raw_payload,
            }
        )

    return parsed


def read_tableau_rows(path: str | Path) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = DictReader(handle)
        for row in reader:
            advisor_name = _clean_value(row.get("Advisor"))
            if advisor_name is None:
                continue

            date_value = _clean_value(row.get("Date"))
            if date_value is not None:
                try:
                    date_value = datetime.strptime(date_value, "%m/%d/%Y").date().isoformat()
                except ValueError:
                    pass

            parsed.append(
                {
                    "advisor_name": advisor_name,
                    "advisor_name_number": _clean_value(row.get("Advisor Name - Number")),
                    "segment": _clean_value(row.get("Segment")),
                    "date": date_value,
                    "area_name": _clean_value(row.get("Area Name")),
                    "region": _clean_value(row.get("Region")),
                    "measure_names": _clean_value(row.get("Measure Names")),
                    "account_count_fund_formatted": _to_float(row.get("Account Count Fund Formatted")),
                    "client_count_fund_formatted": _to_float(row.get("Client Count Fund Formatted")),
                    "fund_formatted": _clean_value(row.get("Fund Formatted")),
                    "approved_to_buy": _clean_value(row.get("Approved to Buy")),
                    "area": _clean_value(row.get("Area")),
                    "division_manager": _clean_value(row.get("Division Manager")),
                    "fund_family": _clean_value(row.get("Fund Family")),
                    "investment_vehicle": _clean_value(row.get("Investment Vehicle")),
                    "pwm": _clean_value(row.get("PWM")),
                    "region_name": _clean_value(row.get("Region Name")),
                    "measure_values": _to_float(row.get("Measure Values")),
                    "raw_payload": {key: _clean_value(value) for key, value in row.items()},
                }
            )

    return parsed


def read_consultant_scorecard_rows(path: str | Path) -> list[dict[str, Any]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = None
    for candidate in workbook.worksheets:
        if "advisor detail" in candidate.title.lower():
            sheet = candidate
            break
    if sheet is None:
        sheet = next((candidate for candidate in workbook.worksheets if candidate.title.lower() != "reference"), workbook.worksheets[0])

    rows = list(sheet.iter_rows(values_only=True))
    header_index, headers = _find_header_row(rows, {"advisor", "advisor#", "area", "region"})

    parsed: list[dict[str, Any]] = []
    for row in rows[header_index + 1 :]:
        row_list = list(row)
        if _is_blank_row(row_list):
            continue

        first_value = _clean_value(row_list[0] if row_list else None)
        if first_value is None or not re.fullmatch(r"\d+", first_value):
            continue

        raw_payload = _row_payload(headers, row_list)
        advisor_name = _find_value_by_header(headers, row_list, "Advisor", exact=True)
        if advisor_name is None:
            continue

        parsed.append(
            {
                "advisor_name": advisor_name,
                "advisor_number": _to_number(_find_value_by_header(headers, row_list, "Advisor#", exact=True)),
                "area": _find_value_by_header(headers, row_list, "Area", exact=True),
                "ro_number": _find_value_by_header(headers, row_list, "RO#", exact=True),
                "region": _find_value_by_header(headers, row_list, "Region", exact=True),
                "division": _find_value_by_header(headers, row_list, "Div", exact=True),
                "base_achievement_level": _find_value_by_header(headers, row_list, "Base Achievement Level", exact=True),
                "etf_completed_and_approved": _find_value_by_header(headers, row_list, "ETF Completed & Approved", exact=True),
                "designation": _find_value_by_header(headers, row_list, "Designation", exact=True),
                "sales_start_date": _find_value_by_header(headers, row_list, "Sales Start Date", exact=True),
                "termination_date": _find_value_by_header(headers, row_list, "Termination Date", exact=True),
                "tenure_category": _find_value_by_header(headers, row_list, "Tenure Category", exact=True),
                "pwm_indicator": _find_value_by_header(headers, row_list, "PWM Indicator", exact=True),
                "dealer_code": _find_value_by_header(headers, row_list, "Dealer Code", exact=True),
                "insurance_expiry_date": _find_value_by_header(headers, row_list, "Insurance Expiry Date", exact=True),
                "key_driver_score": _find_value_by_header(headers, row_list, "Key Driver Score\n(5)", exact=True),
                "client_bp_count": _find_value_by_header(headers, row_list, "Client BP Count", exact=True),
                "assets_under_management": _find_value_by_header(headers, row_list, "Assets under Management (AUM)", exact=True),
                "third_party_assets": _find_value_by_header(headers, row_list, "Third Party Assets (MF, HISA, ETFs)", exact=True),
                "assets_under_administration": _find_value_by_header(headers, row_list, "Assets under Administration (AUA)", exact=True),
                "raw_payload": raw_payload,
            }
        )

    return parsed


def pretty_json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)

