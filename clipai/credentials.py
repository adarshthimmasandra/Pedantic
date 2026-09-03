"""Windows Credential Manager storage and environment fallback.

The API key is never written to source, configuration, or logs. It lives in the
Windows Credential Manager, reached through ``keyring``:

.. code-block:: text

    Service:  Pedanticai
    Username: anthropic_api_key

The older service name ``clipai`` is still read so that installations predating
the rename keep working, and a key found there is migrated on first use.
``ANTHROPIC_API_KEY`` is honored as a fallback, which is convenient for testing
and for machines where the Credential Manager is locked down by policy.
"""

from __future__ import annotations

import logging
import os
import re

log = logging.getLogger(__name__)

SERVICE_NAME = "Pedanticai"
LEGACY_SERVICE_NAMES = ("clipai",)
USERNAME = "anthropic_api_key"
ENV_VAR = "ANTHROPIC_API_KEY"

KEY_PREFIX = "sk-ant-"
MIN_KEY_LENGTH = 20

# Matches Anthropic-looking keys anywhere in a string so they can be redacted.
API_KEY_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_\-]{4,}")
REDACTED = "sk-ant-***REDACTED***"


class CredentialError(Exception):
    """Raised when the credential store cannot be read or written."""


def redact(text: str) -> str:
    """Replace Anthropic-looking keys with a placeholder.

    This is defense in depth for logs and error messages, not permission to log
    secrets in the first place.
    """
    if not text:
        return text
    return API_KEY_PATTERN.sub(REDACTED, str(text))


def looks_like_api_key(value: str | None) -> bool:
    """Cheap local sanity check before spending a network round trip."""
    if not value:
        return False
    candidate = value.strip()
    return candidate.startswith(KEY_PREFIX) and len(candidate) >= MIN_KEY_LENGTH


def _keyring():
    try:
        import keyring
    except ImportError as exc:  # pragma: no cover - keyring is a dependency
        raise CredentialError("keyring is required to store the API key") from exc
    return keyring


def get_from_keyring(service: str = SERVICE_NAME) -> str | None:
    """Read the key from one credential service, or None."""
    try:
        value = _keyring().get_password(service, USERNAME)
    except CredentialError:
        raise
    except Exception:
        log.warning("could not read the %s credential", service, exc_info=False)
        return None
    if value:
        return value.strip()
    return None


def get_from_environment() -> str | None:
    value = os.environ.get(ENV_VAR)
    if value:
        return value.strip()
    return None


def get_api_key(*, allow_environment: bool = True) -> str | None:
    """Find the API key: current service, legacy service, then environment."""
    value = get_from_keyring(SERVICE_NAME)
    if value:
        return value

    for legacy in LEGACY_SERVICE_NAMES:
        value = get_from_keyring(legacy)
        if value:
            log.info("migrating the API key from the %s credential", legacy)
            try:
                set_api_key(value)
            except CredentialError:
                log.warning("could not migrate the API key; using it as-is")
            return value

    if allow_environment:
        value = get_from_environment()
        if value:
            log.info("using the API key from %s", ENV_VAR)
            return value
    return None


def set_api_key(value: str) -> None:
    """Store the API key in the Windows Credential Manager."""
    candidate = (value or "").strip()
    if not candidate:
        raise CredentialError("the API key is empty")
    try:
        _keyring().set_password(SERVICE_NAME, USERNAME, candidate)
    except CredentialError:
        raise
    except Exception as exc:
        raise CredentialError(f"could not store the API key: {exc}") from exc
    log.info("stored the API key in the %s credential", SERVICE_NAME)


def delete_api_key() -> bool:
    """Remove the stored key from the current and legacy services."""
    keyring = _keyring()
    removed = False
    for service in (SERVICE_NAME, *LEGACY_SERVICE_NAMES):
        try:
            keyring.delete_password(service, USERNAME)
            removed = True
        except Exception:
            continue
    if removed:
        log.info("deleted the stored API key")
    return removed


def has_api_key() -> bool:
    return get_api_key() is not None


def source_description() -> str:
    """Where the key currently comes from, for the tray menu and diagnostics."""
    if get_from_keyring(SERVICE_NAME):
        return f"Windows Credential Manager ({SERVICE_NAME})"
    for legacy in LEGACY_SERVICE_NAMES:
        if get_from_keyring(legacy):
            return f"Windows Credential Manager ({legacy})"
    if get_from_environment():
        return f"{ENV_VAR} environment variable"
    return "not configured"
