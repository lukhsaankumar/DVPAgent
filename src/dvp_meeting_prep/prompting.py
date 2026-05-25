from __future__ import annotations

import json
from typing import Any


def build_meeting_prep_prompt(advisor_name: str, source_results: dict[str, list[dict[str, Any]]]) -> str:
    payload = json.dumps(source_results, indent=2, ensure_ascii=False, default=str)
    return f"""You are preparing a DVP meeting prep document for the advisor {advisor_name}.

Use only the data provided below. If a source has no rows, say so explicitly.

Return a concise but useful meeting prep document with these sections:
1. Advisor summary
2. Salesforce highlights
3. Tableau highlights
4. Consultant scorecard highlights
5. Key talking points
6. Open questions or risks
7. Suggested next steps

Source data:
{payload}
"""

