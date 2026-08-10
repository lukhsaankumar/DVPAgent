from __future__ import annotations

from typing import Any
import logging

from .config import Settings, get_settings
from .files import read_salesforce_rows

logger = logging.getLogger("dvp_meeting_prep.data_source")

DEFAULT_CSV_FALLBACK_PATH = "RelatedMaterials/Sample/Salesforce Spreadsheet Input - DummyData.xlsx"


def load_salesforce_source_data(
    settings: Settings | None = None,
    *,
    csv_path: str | None = None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Source-neutral entry point for advisor/task data that used to come
    only from the Excel export.

    Returns the same row shape regardless of DATA_SOURCE (see
    salesforce/normalize.py:LEGACY_ROW_COLUMNS, which mirrors what
    files.py:read_salesforce_rows produces) -- callers such as
    ingest_all_sources() never need to know or branch on which source
    actually produced the rows.

    `DATA_SOURCE=csv` here means "local spreadsheet file fallback", kept for
    local development or when Salesforce access isn't available; the file is
    still an .xlsx export, matching the repo's existing terminology, not a
    literal .csv.
    """
    settings = settings or get_settings()

    if settings.data_source == "salesforce":
        from .salesforce.extraction import run_extraction

        result = run_extraction(settings, dry_run=dry_run)
        return result.legacy_rows

    if settings.data_source == "csv":
        path = csv_path or settings.csv_input_path or DEFAULT_CSV_FALLBACK_PATH
        logger.info("[CONFIG] Data source: csv (path=%s)", path)
        print(f"[CONFIG] Data source: csv ({path})")
        return read_salesforce_rows(path)

    raise RuntimeError(f"Unsupported DATA_SOURCE: {settings.data_source!r}. Expected 'salesforce' or 'csv'.")
