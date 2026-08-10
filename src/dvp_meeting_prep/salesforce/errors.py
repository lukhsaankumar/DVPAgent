from __future__ import annotations


class SalesforceIntegrationError(RuntimeError):
    """Base class for all errors raised by the Salesforce data source."""


class SalesforceConfigError(SalesforceIntegrationError):
    """Bad or missing configuration: env vars, DATA_SOURCE, APP_ENV, auth mode, dates."""


class SalesforceAuthError(SalesforceIntegrationError):
    """Authentication failed, or required credentials for the selected auth mode are missing."""


class SalesforceMetadataError(SalesforceIntegrationError):
    """A configured object or field is missing, not queryable, or not accessible."""


class SalesforceQueryError(SalesforceIntegrationError):
    """A SOQL query failed: syntax error, rate limiting, network interruption, bad response shape."""


class SalesforceValidationError(SalesforceIntegrationError):
    """Extraction results failed a validation check (strict mode only; non-strict logs a warning)."""
