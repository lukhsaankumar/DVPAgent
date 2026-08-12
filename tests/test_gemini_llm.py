from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from google.auth.exceptions import DefaultCredentialsError
from google.genai.errors import APIError

from dvp_meeting_prep import llm


@pytest.fixture(autouse=True)
def _reset_gemini_client_cache():
    llm.get_gemini_client.cache_clear()
    yield
    llm.get_gemini_client.cache_clear()


class FakeResponse:
    def __init__(self, text: str = "Generated Markdown", finish_reason: str | None = "STOP", usage: object | None = None):
        self._text = text
        self.candidates = [MagicMock(finish_reason=finish_reason)] if finish_reason is not None else []
        self.usage_metadata = usage or {"total_token_count": 42}

    @property
    def text(self) -> str:
        return self._text


class FakeModels:
    def __init__(self, responses=None, exceptions=None):
        self.responses = list(responses or [])
        self.exceptions = list(exceptions or [])
        self.calls: list[dict] = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self.exceptions:
            exc = self.exceptions.pop(0)
            if exc is not None:
                raise exc
        if self.responses:
            return self.responses.pop(0)
        return FakeResponse()


class FakeGenaiClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.models = FakeModels()
        self.closed = False

    def close(self):
        self.closed = True


def _fake_client_factory(responses=None, exceptions=None):
    def factory(**kwargs):
        factory.captured = kwargs
        client = FakeGenaiClient(**kwargs)
        client.models = FakeModels(responses=responses, exceptions=exceptions)
        factory.client = client
        return client

    factory.captured = {}
    factory.client = None
    return factory


# --- Client construction ------------------------------------------------------


def test_client_uses_enterprise_true(base_env, monkeypatch):
    factory = _fake_client_factory()
    monkeypatch.setattr(llm.genai, "Client", factory)
    llm.get_gemini_client()
    assert factory.captured["enterprise"] is True


def test_client_receives_configured_project(base_env, monkeypatch):
    base_env.setenv("GOOGLE_CLOUD_PROJECT", "proj-123")
    factory = _fake_client_factory()
    monkeypatch.setattr(llm.genai, "Client", factory)
    llm.get_gemini_client()
    assert factory.captured["project"] == "proj-123"


def test_client_receives_configured_location(base_env, monkeypatch):
    base_env.setenv("GOOGLE_CLOUD_LOCATION", "europe-west1")
    factory = _fake_client_factory()
    monkeypatch.setattr(llm.genai, "Client", factory)
    llm.get_gemini_client()
    assert factory.captured["location"] == "europe-west1"


def test_client_uses_api_version_v1_by_default(base_env, monkeypatch):
    factory = _fake_client_factory()
    monkeypatch.setattr(llm.genai, "Client", factory)
    llm.get_gemini_client()
    assert factory.captured["http_options"].api_version == "v1"


def test_client_has_no_api_key_kwarg(base_env, monkeypatch):
    factory = _fake_client_factory()
    monkeypatch.setattr(llm.genai, "Client", factory)
    llm.get_gemini_client()
    assert "api_key" not in factory.captured


def test_default_credentials_error_raises_gemini_auth_error(base_env, monkeypatch):
    def factory(**kwargs):
        raise DefaultCredentialsError("no ADC found")

    monkeypatch.setattr(llm.genai, "Client", factory)
    with pytest.raises(llm.GeminiAuthError):
        llm.get_gemini_client()


def test_client_is_cached_across_calls(base_env, monkeypatch):
    factory = _fake_client_factory()
    monkeypatch.setattr(llm.genai, "Client", factory)
    first = llm.get_gemini_client()
    second = llm.get_gemini_client()
    assert first is second


# --- Request mapping -----------------------------------------------------------


def test_prompt_maps_to_contents(base_env, monkeypatch):
    factory = _fake_client_factory()
    monkeypatch.setattr(llm.genai, "Client", factory)
    llm.generate_meeting_prep("Prepare notes for Avery Benton")
    assert factory.client.models.calls[0]["contents"] == "Prepare notes for Avery Benton"


def test_system_instruction_maps_correctly(base_env, monkeypatch):
    factory = _fake_client_factory()
    monkeypatch.setattr(llm.genai, "Client", factory)
    llm.generate_meeting_prep("hello")
    assert factory.client.models.calls[0]["config"].system_instruction == llm.SYSTEM_INSTRUCTION


def test_generation_settings_map_correctly(base_env, monkeypatch):
    base_env.setenv("GOOGLE_GENAI_TEMPERATURE", "0.7")
    base_env.setenv("GOOGLE_GENAI_MAX_OUTPUT_TOKENS", "2048")
    factory = _fake_client_factory()
    monkeypatch.setattr(llm.genai, "Client", factory)
    llm.generate_meeting_prep("hello")
    config = factory.client.models.calls[0]["config"]
    assert config.temperature == 0.7
    assert config.max_output_tokens == 2048
    assert config.response_mime_type == "text/plain"


def test_model_name_comes_from_settings(base_env, monkeypatch):
    base_env.setenv("GOOGLE_GENAI_MODEL", "gemini-9000")
    factory = _fake_client_factory()
    monkeypatch.setattr(llm.genai, "Client", factory)
    llm.generate_meeting_prep("hello")
    assert factory.client.models.calls[0]["model"] == "gemini-9000"


# --- Response handling ----------------------------------------------------------


def test_response_text_returns_markdown(base_env, monkeypatch):
    factory = _fake_client_factory(responses=[FakeResponse(text="# Heading\n\nBody")])
    monkeypatch.setattr(llm.genai, "Client", factory)
    assert llm.generate_meeting_prep("hello") == "# Heading\n\nBody"


def test_response_text_is_stripped(base_env, monkeypatch):
    factory = _fake_client_factory(responses=[FakeResponse(text="  \n# Heading\n\n  ")])
    monkeypatch.setattr(llm.genai, "Client", factory)
    assert llm.generate_meeting_prep("hello") == "# Heading"


def test_empty_response_fails_clearly(base_env, monkeypatch):
    factory = _fake_client_factory(responses=[FakeResponse(text="", finish_reason="STOP")])
    monkeypatch.setattr(llm.genai, "Client", factory)
    with pytest.raises(llm.GeminiEmptyResponseError):
        llm.generate_meeting_prep("hello")


def test_safety_blocked_response_is_handled(base_env, monkeypatch):
    factory = _fake_client_factory(responses=[FakeResponse(text="", finish_reason="SAFETY")])
    monkeypatch.setattr(llm.genai, "Client", factory)
    with pytest.raises(llm.GeminiSafetyBlockedError):
        llm.generate_meeting_prep("hello")


# --- Retries and error mapping ---------------------------------------------------


def test_rate_limit_errors_use_bounded_retries(base_env, monkeypatch):
    base_env.setenv("GOOGLE_GENAI_MAX_RETRIES", "2")
    monkeypatch.setattr(llm, "sleep", lambda _seconds: None)
    exc = APIError(429, {"message": "rate limited"})
    factory = _fake_client_factory(exceptions=[exc, exc], responses=[FakeResponse(text="ok after retry")])
    monkeypatch.setattr(llm.genai, "Client", factory)

    result = llm.generate_meeting_prep("hello")

    assert result == "ok after retry"
    assert len(factory.client.models.calls) == 3


def test_rate_limit_raises_after_retries_exhausted(base_env, monkeypatch):
    base_env.setenv("GOOGLE_GENAI_MAX_RETRIES", "1")
    monkeypatch.setattr(llm, "sleep", lambda _seconds: None)
    exc = APIError(429, {"message": "rate limited"})
    factory = _fake_client_factory(exceptions=[exc, exc])
    monkeypatch.setattr(llm.genai, "Client", factory)

    with pytest.raises(llm.GeminiQuotaError):
        llm.generate_meeting_prep("hello")
    assert len(factory.client.models.calls) == 2


def test_server_error_5xx_is_retried(base_env, monkeypatch):
    base_env.setenv("GOOGLE_GENAI_MAX_RETRIES", "1")
    monkeypatch.setattr(llm, "sleep", lambda _seconds: None)
    exc = APIError(503, {"message": "unavailable"})
    factory = _fake_client_factory(exceptions=[exc], responses=[FakeResponse(text="recovered")])
    monkeypatch.setattr(llm.genai, "Client", factory)

    assert llm.generate_meeting_prep("hello") == "recovered"


def test_permission_errors_are_not_retried(base_env, monkeypatch):
    exc = APIError(403, {"message": "permission denied"})
    factory = _fake_client_factory(exceptions=[exc])
    monkeypatch.setattr(llm.genai, "Client", factory)

    with pytest.raises(llm.GeminiPermissionError):
        llm.generate_meeting_prep("hello")
    assert len(factory.client.models.calls) == 1


def test_invalid_argument_errors_are_not_retried(base_env, monkeypatch):
    exc = APIError(400, {"message": "bad request"})
    factory = _fake_client_factory(exceptions=[exc])
    monkeypatch.setattr(llm.genai, "Client", factory)

    with pytest.raises(llm.GeminiConfigError):
        llm.generate_meeting_prep("hello")
    assert len(factory.client.models.calls) == 1


def test_model_not_found_maps_to_config_error(base_env, monkeypatch):
    exc = APIError(404, {"message": "model not found"})
    factory = _fake_client_factory(exceptions=[exc])
    monkeypatch.setattr(llm.genai, "Client", factory)

    with pytest.raises(llm.GeminiConfigError):
        llm.generate_meeting_prep("hello")


# --- Privacy: nothing sensitive in logs -----------------------------------------


def test_prompt_content_does_not_appear_in_logs(base_env, monkeypatch, caplog):
    secret_prompt = "CONFIDENTIAL_ADVISOR_NOTE_XYZ123"
    factory = _fake_client_factory(responses=[FakeResponse(text="normal output")])
    monkeypatch.setattr(llm.genai, "Client", factory)

    with caplog.at_level(logging.DEBUG):
        llm.generate_meeting_prep(secret_prompt)

    assert secret_prompt not in caplog.text


def test_response_content_does_not_appear_in_logs(base_env, monkeypatch, caplog):
    secret_response = "CONFIDENTIAL_RESPONSE_XYZ123"
    factory = _fake_client_factory(responses=[FakeResponse(text=secret_response)])
    monkeypatch.setattr(llm.genai, "Client", factory)

    with caplog.at_level(logging.DEBUG):
        llm.generate_meeting_prep("hello")

    assert secret_response not in caplog.text


def test_credentials_do_not_appear_in_logs(base_env, monkeypatch, caplog):
    factory = _fake_client_factory()
    monkeypatch.setattr(llm.genai, "Client", factory)

    with caplog.at_level(logging.DEBUG):
        llm.get_gemini_client()

    assert "Credentials(" not in caplog.text
    assert "access_token" not in caplog.text.lower()


# --- Lifecycle -------------------------------------------------------------------


def test_client_closes_during_shutdown(base_env, monkeypatch):
    factory = _fake_client_factory()
    monkeypatch.setattr(llm.genai, "Client", factory)
    llm.get_gemini_client()

    llm.close_gemini_client()

    assert factory.client.closed is True


def test_close_is_safe_when_client_never_created(base_env):
    llm.close_gemini_client()  # must not raise
