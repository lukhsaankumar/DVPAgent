from __future__ import annotations

from time import sleep
from typing import Callable, TypeVar
import logging

import requests
from simple_salesforce import Salesforce
from simple_salesforce.exceptions import (
    SalesforceAuthenticationFailed,
    SalesforceError,
    SalesforceMalformedRequest,
    SalesforceRefusedRequest,
    SalesforceResourceNotFound,
)

from ..config import SalesforceConfig
from .errors import SalesforceAuthError, SalesforceConfigError, SalesforceQueryError

logger = logging.getLogger("dvp_meeting_prep.salesforce")

_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
_NON_RETRYABLE_EXCEPTIONS = (
    SalesforceAuthenticationFailed,
    SalesforceMalformedRequest,
    SalesforceRefusedRequest,
    SalesforceResourceNotFound,
)

T = TypeVar("T")


def resolve_domain(app_env: str, instance_url: str | None) -> str:
    """Map APP_ENV to the `domain` kwarg simple-salesforce expects for password auth."""
    if app_env == "sandbox":
        return "test"
    if app_env == "production":
        return "login"
    if app_env == "custom":
        if not instance_url:
            raise SalesforceConfigError(
                "APP_ENV=custom requires SF_INSTANCE_URL to be set so the correct "
                "Salesforce My Domain host can be used for authentication."
            )
        host = instance_url.strip()
        for prefix in ("https://", "http://"):
            if host.startswith(prefix):
                host = host[len(prefix) :]
        host = host.split("/")[0]
        for suffix in (".salesforce.com",):
            if host.endswith(suffix):
                host = host[: -len(suffix)]
        return host
    raise SalesforceConfigError(f"Unsupported APP_ENV: {app_env!r}. Expected sandbox, production, or custom.")


def _log_credential_presence(sf_config: SalesforceConfig) -> None:
    """Log whether required credentials are present, never their values."""
    if sf_config.auth_mode == "password":
        logger.info("SF_USERNAME configured: %s", bool(sf_config.username))
        logger.info("SF_PASSWORD configured: %s", bool(sf_config.password))
        logger.info("SF_SECURITY_TOKEN configured: %s", bool(sf_config.security_token))
    elif sf_config.auth_mode == "access_token":
        logger.info("SF_ACCESS_TOKEN configured: %s", bool(sf_config.access_token))
        logger.info("SF_INSTANCE_URL configured: %s", bool(sf_config.instance_url))


def connect(sf_config: SalesforceConfig, app_env: str) -> Salesforce:
    """Authenticate to Salesforce using the configured auth mode.

    Never raises on missing credentials until this point -- config loading
    itself doesn't require Salesforce credentials to exist (e.g. DATA_SOURCE
    can be csv), so validation is deferred to the moment a connection is
    actually attempted.
    """
    logger.info("[CONNECT] Connecting to Salesforce (app_env=%s, auth_mode=%s)", app_env, sf_config.auth_mode)
    print(f"[CONNECT] Connecting to Salesforce {app_env}")
    _log_credential_presence(sf_config)

    if sf_config.auth_mode == "password":
        missing = [
            name
            for name, value in (
                ("SF_USERNAME", sf_config.username),
                ("SF_PASSWORD", sf_config.password),
                ("SF_SECURITY_TOKEN", sf_config.security_token),
            )
            if not value
        ]
        if missing:
            raise SalesforceAuthError(
                "Missing required credentials for SF_AUTH_MODE=password: "
                f"{', '.join(missing)}. Set them in your env file (see .env.sandbox.example)."
            )
        domain = resolve_domain(app_env, sf_config.instance_url)
        logger.info("[CONNECT] Salesforce domain: %s", domain)
        try:
            client = Salesforce(
                username=sf_config.username,
                password=sf_config.password,
                security_token=sf_config.security_token,
                domain=domain,
            )
        except SalesforceAuthenticationFailed as exc:
            raise SalesforceAuthError(
                f"Salesforce authentication failed (password mode, domain={domain}). "
                "Check SF_USERNAME/SF_PASSWORD/SF_SECURITY_TOKEN and that the security "
                "token matches the current password (tokens reset when the password changes)."
            ) from exc

    elif sf_config.auth_mode == "access_token":
        missing = [
            name
            for name, value in (
                ("SF_ACCESS_TOKEN", sf_config.access_token),
                ("SF_INSTANCE_URL", sf_config.instance_url),
            )
            if not value
        ]
        if missing:
            raise SalesforceAuthError(
                "Missing required credentials for SF_AUTH_MODE=access_token: "
                f"{', '.join(missing)}."
            )
        try:
            client = Salesforce(instance_url=sf_config.instance_url, session_id=sf_config.access_token)
        except SalesforceAuthenticationFailed as exc:
            raise SalesforceAuthError(
                "Salesforce authentication failed (access_token mode). The access token "
                "may be expired -- access tokens are short-lived; password mode is more "
                "reliable for unattended/CLI use."
            ) from exc
    else:
        raise SalesforceConfigError(
            f"Unsupported SF_AUTH_MODE: {sf_config.auth_mode!r}. Expected 'password' or 'access_token'."
        )

    hostname = client.sf_instance
    logger.info("[CONNECT] Salesforce connection successful (instance=%s, api_version=%s)", hostname, client.sf_version)
    print(f"[CONNECT] Salesforce connection successful (instance={hostname})")
    return client


def with_retries(
    func: Callable[..., T],
    *,
    max_attempts: int = 3,
    base_delay_seconds: float = 0.5,
    step_name: str = "salesforce_call",
) -> T:
    """Call `func` with a bounded exponential-backoff retry for transient errors.

    Retries on: HTTP 429/5xx SalesforceError responses, and requests-level
    connection/timeout errors. Does NOT retry authentication failures,
    malformed SOQL, permission failures, or missing resources -- those are
    never going to succeed on retry.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return func()
        except _NON_RETRYABLE_EXCEPTIONS:
            raise
        except SalesforceError as exc:
            status = getattr(exc, "status", None)
            if status not in _RETRYABLE_STATUSES or attempt >= max_attempts:
                raise SalesforceQueryError(f"{step_name} failed (status={status}): {exc}") from exc
            delay = base_delay_seconds * (2 ** (attempt - 1))
            logger.warning("[%s] transient Salesforce error (status=%s), retrying in %.1fs (attempt %d/%d)", step_name, status, delay, attempt, max_attempts)
            sleep(delay)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            if attempt >= max_attempts:
                raise SalesforceQueryError(f"{step_name} failed after {attempt} attempts: {exc}") from exc
            delay = base_delay_seconds * (2 ** (attempt - 1))
            logger.warning("[%s] network error, retrying in %.1fs (attempt %d/%d): %s", step_name, delay, attempt, max_attempts, exc)
            sleep(delay)
