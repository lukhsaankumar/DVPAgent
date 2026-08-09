# DVP Meeting Prep -- web app image.
#
# This container runs the FastAPI app only. It talks to Supabase and OpenAI
# over the network (both configured via environment variables at runtime),
# so there is no database or other service to run alongside it.
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

RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" || exit 1

CMD ["python", "scripts/run_server.py", "--host", "0.0.0.0", "--port", "8000"]
