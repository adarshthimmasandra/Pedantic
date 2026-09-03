"""Credential storage, migration, and redaction."""

from __future__ import annotations

import pytest

from clipai import credentials


class FakeKeyring:
    """A stand-in for the Windows Credential Manager."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        del self.store[(service, username)]


@pytest.fixture
def fake_keyring(monkeypatch):
    keyring = FakeKeyring()
    monkeypatch.setattr(credentials, "_keyring", lambda: keyring)
    monkeypatch.delenv(credentials.ENV_VAR, raising=False)
    return keyring


def test_redaction_and_key_shape_checks():
    redacted = credentials.redact(
        "failed with sk-ant-api03-AAAABBBBCCCCDDDD1234 while calling"
    )
    assert "sk-ant-api03-AAAABBBBCCCCDDDD1234" not in redacted
    assert credentials.REDACTED in redacted
    assert redacted.startswith("failed with ")

    assert credentials.looks_like_api_key("sk-ant-api03-" + "x" * 20) is True
    assert credentials.looks_like_api_key("sk-ant-short") is False
    assert credentials.looks_like_api_key("not-a-key-but-long-enough-to-pass") is False
    assert credentials.looks_like_api_key(None) is False


def test_keys_are_stored_under_the_pedanticai_service(fake_keyring):
    credentials.set_api_key("sk-ant-api03-" + "y" * 20)
    assert (credentials.SERVICE_NAME, credentials.USERNAME) in fake_keyring.store
    assert credentials.get_api_key() == "sk-ant-api03-" + "y" * 20
    assert credentials.SERVICE_NAME in credentials.source_description()

    with pytest.raises(credentials.CredentialError, match="empty"):
        credentials.set_api_key("   ")


def test_legacy_service_keys_are_found_and_migrated(fake_keyring):
    fake_keyring.store[("clipai", credentials.USERNAME)] = "sk-ant-legacy-key-value"

    assert credentials.get_api_key() == "sk-ant-legacy-key-value"
    # Copied forward so the legacy entry is no longer load-bearing.
    assert fake_keyring.store[(credentials.SERVICE_NAME, credentials.USERNAME)] == (
        "sk-ant-legacy-key-value"
    )


def test_the_environment_variable_is_only_a_fallback(fake_keyring, monkeypatch):
    assert credentials.get_api_key() is None
    assert credentials.has_api_key() is False
    assert credentials.source_description() == "not configured"

    monkeypatch.setenv(credentials.ENV_VAR, "sk-ant-from-environment")
    assert credentials.get_api_key() == "sk-ant-from-environment"

    credentials.set_api_key("sk-ant-from-credential-manager")
    assert credentials.get_api_key() == "sk-ant-from-credential-manager"
    assert credentials.get_api_key(allow_environment=False) == (
        "sk-ant-from-credential-manager"
    )
