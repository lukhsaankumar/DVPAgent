from __future__ import annotations

import logging

import pytest

from dvp_meeting_prep.config import SalesforceConfig
from dvp_meeting_prep.salesforce import client as sf_client
from dvp_meeting_prep.salesforce.errors import SalesforceAuthError, SalesforceConfigError


def _sf_config(**overrides) -> SalesforceConfig:
    base = dict(
        auth_mode="password",
        username="user@example.com",
        password="hunter2",
        security_token="tok123",
        access_token=None,
        instance_url=None,
        advisor_object="Account",
        advisor_number_field="Advisor_Number__c",
        practice_lookup_field=None,
        task_link_field="WhatId",
        opportunity_link_field="AccountId",
        advisor_numbers=("17018",),
        task_subjects=("Call", "Virtual Meeting"),
        activity_start_date=None,
        expected_advisor_count=5,
        expected_task_count=83,
        expected_opportunity_count=4,
        advisor_extra_fields=(),
        task_extra_fields=(),
        opportunity_extra_fields=(),
        advisor_field_map={},
        task_field_map={},
    )
    base.update(overrides)
    return SalesforceConfig(**base)


class FakeSalesforceClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.sf_instance = "example-instance.my.salesforce.com"
        self.sf_version = "59.0"


class FakeAuthFailure(Exception):
    pass


def test_sandbox_selects_test_domain():
    assert sf_client.resolve_domain("sandbox", None) == "test"


def test_production_selects_login_domain():
    assert sf_client.resolve_domain("production", None) == "login"


def test_custom_env_requires_instance_url():
    with pytest.raises(SalesforceConfigError):
        sf_client.resolve_domain("custom", None)


def test_custom_domain_derived_from_instance_url():
    assert sf_client.resolve_domain("custom", "https://mycompany.my.salesforce.com") == "mycompany.my"


def test_unsupported_app_env_raises():
    with pytest.raises(SalesforceConfigError):
        sf_client.resolve_domain("staging", None)


def test_password_mode_connects_with_resolved_domain(monkeypatch):
    captured: dict = {}

    def fake_salesforce(**kwargs):
        captured.update(kwargs)
        return FakeSalesforceClient(**kwargs)

    monkeypatch.setattr(sf_client, "Salesforce", fake_salesforce)
    client = sf_client.connect(_sf_config(), "sandbox")

    assert captured["domain"] == "test"
    assert captured["username"] == "user@example.com"
    assert captured["password"] == "hunter2"
    assert captured["security_token"] == "tok123"
    assert isinstance(client, FakeSalesforceClient)


def test_production_uses_login_domain(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(sf_client, "Salesforce", lambda **kwargs: (captured.update(kwargs), FakeSalesforceClient(**kwargs))[1])
    sf_client.connect(_sf_config(), "production")
    assert captured["domain"] == "login"


def test_access_token_mode_uses_instance_url_and_session_id(monkeypatch):
    captured: dict = {}

    def fake_salesforce(**kwargs):
        captured.update(kwargs)
        return FakeSalesforceClient(**kwargs)

    monkeypatch.setattr(sf_client, "Salesforce", fake_salesforce)
    config = _sf_config(
        auth_mode="access_token",
        username=None,
        password=None,
        security_token=None,
        access_token="00Dxx!abc",
        instance_url="https://example.my.salesforce.com",
    )
    sf_client.connect(config, "production")

    assert captured["instance_url"] == "https://example.my.salesforce.com"
    assert captured["session_id"] == "00Dxx!abc"
    assert "domain" not in captured


def test_missing_password_credentials_raise_clear_error(monkeypatch):
    monkeypatch.setattr(sf_client, "Salesforce", lambda **kwargs: FakeSalesforceClient(**kwargs))
    with pytest.raises(SalesforceAuthError, match="SF_PASSWORD"):
        sf_client.connect(_sf_config(password=None), "sandbox")


def test_missing_access_token_credentials_raise_clear_error(monkeypatch):
    monkeypatch.setattr(sf_client, "Salesforce", lambda **kwargs: FakeSalesforceClient(**kwargs))
    config = _sf_config(auth_mode="access_token", access_token=None, instance_url=None)
    with pytest.raises(SalesforceAuthError, match="SF_ACCESS_TOKEN"):
        sf_client.connect(config, "sandbox")


def test_unsupported_auth_mode_raises(monkeypatch):
    monkeypatch.setattr(sf_client, "Salesforce", lambda **kwargs: FakeSalesforceClient(**kwargs))
    config = _sf_config(auth_mode="oauth")
    with pytest.raises(SalesforceConfigError, match="SF_AUTH_MODE"):
        sf_client.connect(config, "sandbox")


def test_authentication_failure_wrapped_as_auth_error(monkeypatch):
    from simple_salesforce.exceptions import SalesforceAuthenticationFailed

    def raise_auth_failure(**kwargs):
        raise SalesforceAuthenticationFailed("INVALID_LOGIN", "bad creds")

    monkeypatch.setattr(sf_client, "Salesforce", raise_auth_failure)
    with pytest.raises(SalesforceAuthError):
        sf_client.connect(_sf_config(), "sandbox")


def test_secrets_never_appear_in_logs(monkeypatch, caplog):
    monkeypatch.setattr(sf_client, "Salesforce", lambda **kwargs: FakeSalesforceClient(**kwargs))
    config = _sf_config(password="super-secret-password", security_token="super-secret-token")
    with caplog.at_level(logging.DEBUG):
        sf_client.connect(config, "sandbox")
    assert "super-secret-password" not in caplog.text
    assert "super-secret-token" not in caplog.text


def test_secrets_never_printed(monkeypatch, capsys):
    monkeypatch.setattr(sf_client, "Salesforce", lambda **kwargs: FakeSalesforceClient(**kwargs))
    config = _sf_config(password="super-secret-password", security_token="super-secret-token")
    sf_client.connect(config, "sandbox")
    out = capsys.readouterr().out
    assert "super-secret-password" not in out
    assert "super-secret-token" not in out
