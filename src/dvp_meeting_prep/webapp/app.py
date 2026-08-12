from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..config import get_settings
from ..db import get_database
from ..llm import close_gemini_client, get_gemini_client
from .api import router as api_router

logging.basicConfig(level=logging.INFO)

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Fail fast on a broken .env instead of surfacing a confusing 502 on the
# first request that happens to touch the database or Gemini.
get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Safe to run on every startup: creates missing tables/indexes and
    # applies pending migrations, never drops or deletes anything (see
    # db.py:ensure_schema_ready). Runs before the app starts serving requests.
    get_database().ensure_schema_ready()

    # One Gemini client for the process lifetime (not one per request) --
    # created eagerly so a missing/invalid ADC setup fails at startup rather
    # than on the first meeting-prep request.
    get_gemini_client()
    try:
        yield
    finally:
        close_gemini_client()


app = FastAPI(title="DVP Meeting Prep", lifespan=lifespan)
app.include_router(api_router)
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


@app.get("/")
def serve_home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/upload")
def serve_upload_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "upload.html")
