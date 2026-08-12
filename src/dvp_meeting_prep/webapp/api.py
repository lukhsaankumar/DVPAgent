from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..advisors import search_advisor_names
from ..config import get_settings
from ..db import Database, get_database
from ..docx_export import markdown_to_docx_bytes
from ..files import slugify_filename
from ..ingest import ingest_consultant_scorecard_upload, ingest_tableau_upload
from ..llm import generate_meeting_prep
from ..prompting import build_meeting_prep_prompt
from ..query import fetch_all_sources_for_advisor

logger = logging.getLogger("dvp_meeting_prep.webapp")

router = APIRouter(prefix="/api")

MAX_UPLOAD_BYTES = 25 * 1024 * 1024


class AdvisorSearchResponse(BaseModel):
    advisors: list[str]


class MeetingPrepRequest(BaseModel):
    advisor_name: str = Field(min_length=1, max_length=200)


class UploadResult(BaseModel):
    source_type: str
    file_name: str
    rows_parsed: int
    rows_inserted: int
    rows_skipped_duplicate: int


def _record_upload_batch(
    database: Database, *, source_type: str, file_name: str, counts: dict[str, int] | None, status: str, error_message: str | None
) -> None:
    try:
        with database.write() as conn:
            conn.execute(
                """
                INSERT INTO upload_batches
                    (source_type, file_name, rows_parsed, rows_inserted, rows_skipped_duplicate, status, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_type,
                    file_name,
                    (counts or {}).get("rows_parsed", 0),
                    (counts or {}).get("rows_inserted", 0),
                    (counts or {}).get("rows_skipped_duplicate", 0),
                    status,
                    error_message,
                ),
            )
    except Exception:
        logger.exception("Failed to record upload batch audit row for %s (%s)", file_name, source_type)


async def _write_upload_to_temp_file(upload: UploadFile, suffix: str) -> Path:
    contents = await upload.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Uploaded file is too large (25 MB limit).")

    with NamedTemporaryFile(delete=False, suffix=suffix) as handle:
        handle.write(contents)
        return Path(handle.name)


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        # Best-effort cleanup only; a locked temp file (e.g. openpyxl holding
        # it open on Windows) should never turn a successful request into a
        # 500. The OS temp directory will reclaim it eventually.
        logger.warning("Could not delete temp upload file %s", path)


@router.get("/health")
def health() -> dict[str, str]:
    database = get_database()
    result = database.health_check()
    if not result.get("ok"):
        raise HTTPException(status_code=503, detail=f"Database unavailable: {result.get('error')}")
    return {"status": "ok"}


@router.get("/advisors", response_model=AdvisorSearchResponse)
def get_advisors(q: str = Query(default="", max_length=200), limit: int = Query(default=20, ge=1, le=100)) -> AdvisorSearchResponse:
    database = get_database()
    try:
        matches = search_advisor_names(database, q, limit=limit)
    except Exception as exc:
        logger.exception("Advisor search failed")
        raise HTTPException(status_code=502, detail=f"Could not search advisors: {exc}") from exc
    return AdvisorSearchResponse(advisors=matches)


@router.post("/uploads/tableau", response_model=UploadResult)
async def upload_tableau(file: UploadFile) -> UploadResult:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Tableau uploads must be a .csv file.")

    database = get_database()
    temp_path = await _write_upload_to_temp_file(file, suffix=".csv")
    try:
        counts = ingest_tableau_upload(database, temp_path)
    except Exception as exc:
        logger.exception("Tableau upload ingestion failed for %s", file.filename)
        _record_upload_batch(database, source_type="tableau", file_name=file.filename, counts=None, status="error", error_message=str(exc))
        raise HTTPException(status_code=422, detail=f"Could not ingest Tableau file: {exc}") from exc
    finally:
        _safe_unlink(temp_path)

    _record_upload_batch(database, source_type="tableau", file_name=file.filename, counts=counts, status="success", error_message=None)
    return UploadResult(source_type="tableau", file_name=file.filename, **counts)


@router.post("/uploads/consultant-scorecard", response_model=UploadResult)
async def upload_consultant_scorecard(file: UploadFile) -> UploadResult:
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=400, detail="Consultant scorecard uploads must be an .xlsx file.")

    database = get_database()
    temp_path = await _write_upload_to_temp_file(file, suffix=".xlsx")
    try:
        counts = ingest_consultant_scorecard_upload(database, temp_path, source_file_name=file.filename)
    except Exception as exc:
        logger.exception("Consultant scorecard upload ingestion failed for %s", file.filename)
        _record_upload_batch(database, source_type="consultant_scorecard", file_name=file.filename, counts=None, status="error", error_message=str(exc))
        raise HTTPException(status_code=422, detail=f"Could not ingest consultant scorecard file: {exc}") from exc
    finally:
        _safe_unlink(temp_path)

    _record_upload_batch(database, source_type="consultant_scorecard", file_name=file.filename, counts=counts, status="success", error_message=None)
    return UploadResult(
        source_type="consultant_scorecard",
        file_name=file.filename,
        rows_parsed=counts["rows_parsed"],
        rows_inserted=counts["rows_inserted"],
        rows_skipped_duplicate=counts["rows_skipped_duplicate"],
    )


def _record_meeting_prep_document(database: Database, *, advisor_name: str, prompt: str, response: str) -> None:
    try:
        with database.write() as conn:
            conn.execute(
                "INSERT INTO meeting_prep_documents (advisor_name, prompt, response) VALUES (?, ?, ?)",
                (advisor_name, prompt, response),
            )
    except Exception:
        logger.exception("Failed to record meeting_prep_documents audit row for %s", advisor_name)


@router.post("/meeting-prep")
def create_meeting_prep(payload: MeetingPrepRequest) -> Response:
    advisor_name = payload.advisor_name.strip()
    if not advisor_name:
        raise HTTPException(status_code=400, detail="advisor_name is required.")

    settings = get_settings()
    database = get_database()

    try:
        source_results = fetch_all_sources_for_advisor(database, advisor_name)
    except Exception as exc:
        logger.exception("Failed to fetch source data for advisor %s", advisor_name)
        raise HTTPException(status_code=502, detail=f"Could not fetch advisor data: {exc}") from exc

    if not any(source_results.values()):
        raise HTTPException(status_code=404, detail=f"No data found for advisor '{advisor_name}' in any source.")

    prompt = build_meeting_prep_prompt(advisor_name, source_results)

    try:
        markdown_response = generate_meeting_prep(prompt)
    except Exception as exc:
        logger.exception("LLM generation failed for advisor %s", advisor_name)
        raise HTTPException(status_code=502, detail=f"Meeting prep generation failed: {exc}") from exc

    if settings.gemini.store_audit_content:
        _record_meeting_prep_document(database, advisor_name=advisor_name, prompt=prompt, response=markdown_response)

    try:
        docx_bytes = markdown_to_docx_bytes(markdown_response, title=f"DVP Meeting Prep: {advisor_name}")
    except Exception as exc:
        logger.exception("Failed to render meeting prep docx for advisor %s", advisor_name)
        raise HTTPException(status_code=500, detail=f"Could not generate Word document: {exc}") from exc

    date_stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    file_name = f"{slugify_filename(advisor_name)}_meeting_prep_{date_stamp}.docx"

    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )
