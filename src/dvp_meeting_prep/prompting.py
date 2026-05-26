from __future__ import annotations

import json
from typing import Any


SALESFORCE_KEYS = [
    "advisor_name",
    "advisor_number",
    "task_subtype",
    "subject",
    "comments",
    "interaction_type",
    "completed_date_time",
    "district_vp_wholesaling",
    "pwm",
    "book_size",
    "assets_under_management",
    "new_business_ytd",
    "created_date",
    "start_date",
    "status",
    "area",
    "region_office_number",
    "assigned",
]

TABLEAU_KEYS = [
    "advisor_name",
    "advisor_name_number",
    "segment",
    "date",
    "area_name",
    "region",
    "measure_names",
    "account_count_fund_formatted",
    "client_count_fund_formatted",
    "fund_formatted",
    "approved_to_buy",
    "area",
    "division_manager",
    "fund_family",
    "investment_vehicle",
    "pwm",
    "region_name",
    "measure_values",
]

TABLEAU_ALLOWED_MEASURES = {
    "Total AUA",
    "3rd Party Assets",
    "Yearly Third Party ETF and MF Sales",
    "Yearly Gross Sales",
}

MONTHLY_KEYS = [
    "source_file",
    "report_date",
    "advisor_number",
    "advisor_name",
    "area",
    "ro_number",
    "region",
    "division",
    "base_achievement_level",
    "etf_completed_approved",
    "designation",
    "sales_start_date",
    "termination_date",
    "tenure_category",
    "pwm_indicator",
    "dealer_code",
    "insurance_expiry_date",
    "key_driver_score",
]


def _compact_rows(rows: list[dict[str, Any]], keys: list[str], *, max_rows: int | None = None) -> list[dict[str, Any]]:
    selected = rows if max_rows is None else rows[:max_rows]
    compacted: list[dict[str, Any]] = []
    for row in selected:
        compacted.append({key: row.get(key) for key in keys if key in row})
    return compacted


def _group_consultant_metrics(metric_rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for row in metric_rows:
        group = str(row.get("metric_group") or "Other")
        period = str(row.get("metric_period") or "Unspecified")
        bucket = grouped.setdefault(group, {})
        period_bucket = bucket.setdefault(period, [])
        period_bucket.append(
            {
                "metric_name": row.get("metric_name"),
                "value_numeric": row.get("value_numeric"),
                "value_text": row.get("value_text"),
                "unit": row.get("unit"),
            }
        )
    return grouped


def _build_salesforce_prompt_section(rows: list[dict[str, Any]]) -> dict[str, Any]:
    compact_rows = _compact_rows(rows, SALESFORCE_KEYS)
    return {
        "source": "salesforce_data",
        "row_count": len(rows),
        "recent_interactions": compact_rows[:3],
    }


def _build_tableau_prompt_section(rows: list[dict[str, Any]]) -> dict[str, Any]:
    filtered_rows = [row for row in rows if row.get("measure_names") in TABLEAU_ALLOWED_MEASURES]
    compact_rows = _compact_rows(filtered_rows, TABLEAU_KEYS)

    context = None
    if compact_rows:
        first = compact_rows[0]
        context = {
            "source": "tableau_data",
            "advisor_name_number": first.get("advisor_name_number"),
            "segment": first.get("segment"),
            "area_name": first.get("area_name"),
            "region": first.get("region"),
            "division_manager": first.get("division_manager"),
            "pwm": first.get("pwm"),
        }

    metrics = [
        {
            "source": "tableau_data",
            "date": row.get("date"),
            "measure_name": row.get("measure_names"),
            "measure_value": row.get("measure_values"),
            "fund_formatted": row.get("fund_formatted"),
            "fund_family": row.get("fund_family"),
            "investment_vehicle": row.get("investment_vehicle"),
            "approved_to_buy": row.get("approved_to_buy"),
            "account_count": row.get("account_count_fund_formatted"),
            "client_count": row.get("client_count_fund_formatted"),
        }
        for row in compact_rows
    ]

    return {
        "source": "tableau_data",
        "row_count": len(rows),
        "filtered_row_count": len(filtered_rows),
        "context": context,
        "metrics": metrics,
    }


RELEVANT_METRIC_KEYS: list[tuple[str, str | None, str]] = [
    ("Assets", "Month", "Assets under Management (AUM)"),
    ("Assets", "Month", "Third Party Assets (MF, HISA, ETFs)"),
    ("Assets", "Month", "Assets under Administration (AUA)"),
    ("Net Contributions", "Month", "Net Total Contribution"),
    ("Net Contributions", "YTD", "Net Total Contribution"),
    ("Net Contributions", "Month", "Net Total Contributions % of AUA"),
    ("Net Contributions", "YTD", "Net Total Contributions % of AUA"),
    ("Key Driver Score", None, "Key Driver Score\n(5)"),
    ("Client", "Month", "Client BP Count"),
    ("Growth", "Month", "New Inv FG - $1M+ (count)"),
    ("Family Group", None, "Total"),
]


def _build_consultant_relevant_section(consultant_monthly: list[dict[str, Any]], consultant_metrics: list[dict[str, Any]]) -> dict[str, Any]:
    advisor_profile = _compact_rows(consultant_monthly, MONTHLY_KEYS, max_rows=1)

    selected: list[dict[str, Any]] = []
    for metric_group, metric_period, metric_name in RELEVANT_METRIC_KEYS:
        for row in consultant_metrics:
            if row.get("metric_group") != metric_group:
                continue
            if metric_period is not None and row.get("metric_period") != metric_period:
                continue
            if row.get("metric_name") != metric_name:
                continue
            selected.append(
                {
                    "metric_group": row.get("metric_group"),
                    "metric_period": row.get("metric_period"),
                    "metric_name": row.get("metric_name"),
                    "value_numeric": row.get("value_numeric"),
                    "value_text": row.get("value_text"),
                    "unit": row.get("unit"),
                }
            )
            break

    return {
        "source_monthly": "consultant_scorecard_monthly",
        "source_metric": "consultant_scorecard_metric",
        "advisor_profile": advisor_profile,
        "key_metrics": selected,
    }


def build_prompt_payload(advisor_name: str, source_results: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    consultant_monthly = source_results.get("consultant_scorecard_monthly", [])
    consultant_metrics = source_results.get("consultant_scorecard_metric", [])
    salesforce_rows = source_results.get("salesforce_data", [])
    tableau_rows = source_results.get("tableau_data", [])

    payload: dict[str, Any] = {
        "advisor_name": advisor_name,
        "source_counts": {
            "salesforce_data": len(salesforce_rows),
            "tableau_data": len(tableau_rows),
            "consultant_scorecard_monthly": len(consultant_monthly),
            "consultant_scorecard_metric": len(consultant_metrics),
        },
        "prompt_payload": {
            "salesforce": _build_salesforce_prompt_section(salesforce_rows),
            "tableau": _build_tableau_prompt_section(tableau_rows),
            "consultant_scorecard": _build_consultant_relevant_section(consultant_monthly, consultant_metrics),
        },
    }
    return payload


def build_meeting_prep_prompt(advisor_name: str, source_results: dict[str, list[dict[str, Any]]]) -> str:
    payload = json.dumps(build_prompt_payload(advisor_name, source_results), indent=2, ensure_ascii=False, default=str)
    return f"""You are preparing a DVP meeting prep document for the advisor {advisor_name}.

Use only the data provided below. If a source has no rows, say so explicitly.

The JSON is organized by source. Use the named source buckets directly and cite them explicitly in your writing:
- `salesforce` for Salesforce spreadsheet information
- `tableau` for Tableau metrics
- `consultant_scorecard` for consultant scorecard information

Do not invent facts or mix fields across sources unless the source data clearly supports it. Prefer the most relevant fields and metrics only.

Return a concise but useful meeting prep document with these sections:
1. Advisor summary
2. Salesforce highlights
3. Tableau highlights
4. Consultant scorecard highlights
5. Key talking points
6. Open questions or risks
7. Suggested next steps

Format the final answer as clean Markdown only. Use clear headings, short bullet points, and concise prose. Make the source of each fact obvious in the wording where helpful.

Source data:
{payload}
"""

