from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..config import get_settings
from .api import router as api_router

logging.basicConfig(level=logging.INFO)

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Fail fast on a broken .env instead of surfacing a confusing 502 on the
# first request that happens to touch Supabase or OpenAI.
get_settings()

app = FastAPI(title="DVP Meeting Prep")
app.include_router(api_router)
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


@app.get("/")
def serve_home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/upload")
def serve_upload_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "upload.html")
