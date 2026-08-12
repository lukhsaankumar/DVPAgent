# DVP Meeting Prep -- web app image.
#
# This container runs the FastAPI app. It stores its application data in a
# local SQLite database file under /app/data (mount a volume there for
# persistence across container restarts -- see docker-compose.yml) and talks
# to Gemini Enterprise over the network using Google Application Default
# Credentials resolved at runtime. No other service needs to run alongside
# it. The image never bakes in a populated database or any credentials.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install runtime dependencies first so this layer is cached across code
# changes. requirements.txt intentionally excludes pytest/playwright
# (requirements-dev.txt) -- the runtime image doesn't need a browser.
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY src/ ./src/
COPY scripts/ ./scripts/
COPY sql/ ./sql/

# /app/data holds the SQLite database file (SQLITE_DB_PATH defaults to
# data/dvp_meeting_prep.sqlite3, resolved relative to the project root).
# Creating it here -- owned by appuser -- means a fresh named Docker volume
# mounted at /app/data inherits correct write permissions on first use.
# The directory is intentionally left empty: no database is baked into the
# image.
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# /api/health checks SQLite readiness (see webapp/api.py + db.py
# health_check()) in addition to basic liveness.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" || exit 1

# The FastAPI lifespan handler calls ensure_schema_ready() before the app
# starts accepting requests, so the schema is created/migrated safely on
# every boot -- no separate init step is required here.
CMD ["python", "scripts/run_server.py", "--host", "0.0.0.0", "--port", "8000"]
