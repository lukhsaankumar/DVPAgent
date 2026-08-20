from __future__ import annotations

from functools import lru_cache
from random import uniform
from time import monotonic, sleep
import logging

from google import genai
from google.auth import default as _resolve_adc_credentials
from google.auth.exceptions import DefaultCredentialsError, RefreshError
from google.genai import types
from google.genai.errors import APIError

from .config import get_settings

logger = logging.getLogger("dvp_meeting_prep.llm")

SYSTEM_INSTRUCTION = "You write clear internal meeting prep documents for advisor conversations."

# HTTP statuses worth a bounded retry: rate limiting and transient server
# errors. Anything else (auth, permission, bad argument, not found) will
# never succeed on retry, so it's raised immediately instead.
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

_ACCEPTABLE_FINISH_REASONS = {"STOP", "FINISH_REASON_UNSPECIFIED", "MAX_TOKENS"}


class GeminiError(RuntimeError):
    """Base class for all errors raised by the Gemini integration."""


class GeminiAuthError(GeminiError):
    """Application Default Credentials are missing, invalid, or could not be refreshed."""


class GeminiPermissionError(GeminiError):
    """The authenticated identity is not authorized for this project/model/API."""


class GeminiConfigError(GeminiError):
    """Invalid project, location, or model configuration (or the resource doesn't exist)."""


class GeminiQuotaError(GeminiError):
    """Rate limited or quota exhausted, and retries were exhausted."""


class GeminiEmptyResponseError(GeminiError):
    """Gemini returned no usable text."""


class GeminiSafetyBlockedError(GeminiError):
    """Gemini declined to answer (safety, recitation, or other non-STOP finish reason)."""


@lru_cache(maxsize=1)
def get_gemini_client() -> genai.Client:
    """Create (and cache) a single Gemini Enterprise client for the process lifetime.

    Authenticates via Google Application Default Credentials -- the SDK
    discovers and refreshes credentials itself; this code never fetches,
    stores, or logs an access token. See docs/TESTING_GEMINI_SQLITE_MIGRATION.md
    for the external `gcloud auth application-default login` setup this
    expects (that setup happens outside this application, never in code).
    """
    settings = get_settings()
    gemini = settings.gemini
    logger.info(
        "[LLM] Creating Gemini client: provider=%s project=%s location=%s model=%s api_version=%s",
        gemini.provider,
        gemini.project,
        gemini.location,
        gemini.model,
        gemini.api_version,
    )
    # Resolving credentials ourselves (rather than always letting the SDK do
    # it lazily) lets a quota project be attached up front -- gcloud-CLI-derived
    # ADC has none by default, and google-auth prints a "quota exceeded"/"API
    # not enabled" warning on every request until one is set (it only warns
    # when quota_project_id is falsy). This is best-effort, though: if ADC
    # genuinely isn't available yet (e.g. local dev before `gcloud auth
    # application-default login`), don't let that block the app from
    # starting -- fall back to constructing the client without explicit
    # credentials, exactly as before this fix, so the SDK resolves ADC lazily
    # on the first real request and the actual auth failure surfaces there
    # (via _classify_error() below) instead of at startup.
    credentials = None
    try:
        credentials, _ = _resolve_adc_credentials(quota_project_id=gemini.project)
    except DefaultCredentialsError:
        logger.debug("[LLM] Could not pre-resolve ADC to attach a quota project; deferring to first real request")

    try:
        return genai.Client(
            enterprise=True,
            credentials=credentials,
            project=gemini.project,
            location=gemini.location,
            http_options=types.HttpOptions(
                api_version=gemini.api_version,
                timeout=gemini.request_timeout_seconds * 1000,
            ),
        )
    except DefaultCredentialsError as exc:
        raise GeminiAuthError(
            "Google Application Default Credentials were not found. Run "
            "'gcloud auth application-default login' (see "
            "docs/TESTING_GEMINI_SQLITE_MIGRATION.md) and try again."
        ) from exc


def close_gemini_client() -> None:
    """Close the cached client and drop it from the cache. Safe to call even if never created.

    Called from the FastAPI shutdown handler and can be called at the end of
    CLI scripts; harmless to call more than once.
    """
    if get_gemini_client.cache_info().currsize == 0:
        return
    client = get_gemini_client()
    get_gemini_client.cache_clear()
    try:
        client.close()
    except Exception:
        logger.exception("[LLM] Error closing Gemini client")


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (DefaultCredentialsError, RefreshError)):
        return False
    if isinstance(exc, APIError):
        return getattr(exc, "code", None) in _RETRYABLE_STATUS_CODES
    # Network-transport errors (connection reset, read timeout) don't always
    # surface as APIError -- fall back to a name-based heuristic for those.
    name = type(exc).__name__.lower()
    return "timeout" in name or "connectionerror" in name or "connectionreset" in name


def _classify_error(exc: Exception) -> GeminiError:
    """Map a google-genai/auth exception to a typed, prompt-content-free error."""
    if isinstance(exc, DefaultCredentialsError):
        return GeminiAuthError("Google Application Default Credentials were not found or are invalid.")
    if isinstance(exc, RefreshError):
        return GeminiAuthError("Google Application Default Credentials could not be refreshed (they may be expired or revoked).")
    if isinstance(exc, APIError):
        code = getattr(exc, "code", None)
        if code in (401,):
            return GeminiAuthError(f"Gemini request was unauthenticated (HTTP {code}).")
        if code == 403:
            return GeminiPermissionError(
                f"Permission denied calling Gemini (HTTP {code}). Confirm the Vertex AI API is enabled on "
                "the configured project and the authenticated identity has the required IAM role."
            )
        if code == 404:
            return GeminiConfigError(
                f"Model or resource not found (HTTP {code}). Check GOOGLE_GENAI_MODEL, "
                "GOOGLE_CLOUD_PROJECT, and GOOGLE_CLOUD_LOCATION."
            )
        if code == 400:
            return GeminiConfigError(f"Invalid request to Gemini (HTTP {code}). Check the configured project/location/model.")
        if code == 429:
            return GeminiQuotaError(f"Gemini rate limit or quota exceeded (HTTP {code}), retries exhausted.")
        return GeminiError(f"Gemini request failed (HTTP {code}).")
    return GeminiError(f"Unexpected Gemini error: {type(exc).__name__}")


def generate_meeting_prep(prompt: str) -> str:
    """Generate the meeting-prep Markdown document for the given prompt.

    Preserves the pre-migration contract (generate_meeting_prep(prompt) ->
    str of Markdown, raising on failure) so prompting.py/webapp/scripts don't
    need to change. Never logs the prompt or response content.
    """
    settings = get_settings()
    gemini = settings.gemini
    client = get_gemini_client()

    generation_config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        temperature=gemini.temperature,
        max_output_tokens=gemini.max_output_tokens,
        response_mime_type="text/plain",
    )

    prompt_chars = len(prompt)
    max_attempts = gemini.max_retries + 1
    attempt = 0
    start = monotonic()

    while True:
        attempt += 1
        try:
            response = client.models.generate_content(model=gemini.model, contents=prompt, config=generation_config)
            break
        except Exception as exc:  # noqa: BLE001 -- always reclassified below; never re-raised with prompt content
            if _is_retryable(exc) and attempt < max_attempts:
                delay = min(2 ** (attempt - 1), 20) + uniform(0, 0.5)
                logger.warning(
                    "[LLM] transient error on attempt %d/%d (%s), retrying in %.1fs",
                    attempt,
                    max_attempts,
                    type(exc).__name__,
                    delay,
                )
                sleep(delay)
                continue
            duration = monotonic() - start
            classified = _classify_error(exc)
            logger.error(
                "[LLM] request failed after %d attempt(s) in %.2fs: %s (root cause: %s)",
                attempt,
                duration,
                type(classified).__name__,
                type(exc).__name__,
            )
            raise classified from exc

    duration = monotonic() - start

    finish_reason = None
    usage_metadata = None
    try:
        if response.candidates:
            finish_reason = getattr(response.candidates[0], "finish_reason", None)
        usage_metadata = response.usage_metadata
    except Exception:
        logger.debug("[LLM] Could not read finish_reason/usage_metadata from response", exc_info=True)

    text = (response.text or "").strip()

    logger.info(
        "[LLM] provider=%s model=%s duration=%.2fs attempts=%d prompt_chars=%d response_chars=%d finish_reason=%s usage_metadata=%s",
        gemini.provider,
        gemini.model,
        duration,
        attempt,
        prompt_chars,
        len(text),
        finish_reason,
        usage_metadata,
    )

    if not text:
        finish_reason_name = str(finish_reason).upper() if finish_reason is not None else None
        if finish_reason_name is not None and finish_reason_name not in _ACCEPTABLE_FINISH_REASONS:
            raise GeminiSafetyBlockedError(f"Gemini did not return usable text (finish_reason={finish_reason}).")
        raise GeminiEmptyResponseError("Gemini returned an empty response.")

    return text
