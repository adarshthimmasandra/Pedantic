"""Rotating logging and secret redaction."""

from __future__ import annotations

import logging

import pytest

from clipai import paths
from clipai.credentials import REDACTED
from clipai.logging_setup import configure_logging, reset_logging


@pytest.fixture(autouse=True)
def clean_logging():
    reset_logging()
    yield
    reset_logging()


def test_api_keys_never_reach_the_log_file(isolated_home):
    path = configure_logging(debug=True, console=False)
    assert path == paths.log_path()

    log = logging.getLogger("clipai.test")
    secret = "sk-ant-api03-SECRETSECRETSECRET1234"
    log.info("stored %s for later", secret)
    log.info("failure: %s", RuntimeError(f"rejected {secret}"))
    try:
        raise RuntimeError(f"traceback carrying {secret}")
    except RuntimeError:
        log.exception("unexpected failure")

    for handler in logging.getLogger().handlers:
        handler.flush()

    contents = path.read_text(encoding="utf-8")
    assert secret not in contents
    assert contents.count(REDACTED) >= 3


def test_configure_logging_is_idempotent_and_sets_the_level(isolated_home):
    configure_logging(debug=False, console=False)
    assert logging.getLogger().level == logging.INFO
    handler_count = len(logging.getLogger().handlers)

    configure_logging(debug=True, console=False)
    assert logging.getLogger().level == logging.DEBUG
    assert len(logging.getLogger().handlers) == handler_count
