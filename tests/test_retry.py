"""Retry policy and error classification."""

from __future__ import annotations

import ssl

import pytest

from clipai.backends.retry import (
    ErrorCategory,
    RetryPolicy,
    classify,
    retry_after_seconds,
    run_with_retry,
)


class FakeResponse:
    def __init__(self, status_code: int, headers: dict[str, str] | None = None):
        self.status_code = status_code
        self.headers = headers or {}


class FakeApiError(Exception):
    def __init__(self, status_code: int, headers: dict[str, str] | None = None):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code
        self.response = FakeResponse(status_code, headers)


def test_status_codes_map_to_categories_and_retry_decisions():
    assert classify(FakeApiError(401)) is ErrorCategory.AUTH
    assert classify(FakeApiError(400)) is ErrorCategory.REQUEST
    assert classify(FakeApiError(429)) is ErrorCategory.RATE_LIMIT
    assert classify(FakeApiError(500)) is ErrorCategory.SERVER
    assert classify(FakeApiError(529)) is ErrorCategory.OVERLOADED
    assert classify(FakeApiError(407)) is ErrorCategory.PROXY

    # 429, 500, and 529 are retried; 400 and 401 never are.
    assert classify(FakeApiError(429)).retryable is True
    assert classify(FakeApiError(500)).retryable is True
    assert classify(FakeApiError(529)).retryable is True
    assert classify(FakeApiError(400)).retryable is False
    assert classify(FakeApiError(401)).retryable is False


def test_transport_failures_are_classified_by_type_message_and_cause():
    certificate = ssl.SSLCertVerificationError(
        "certificate verify failed: unable to get local issuer certificate"
    )
    assert classify(certificate) is ErrorCategory.CERTIFICATE
    assert classify(certificate).retryable is False

    # A certificate problem is often wrapped in a generic connection error.
    wrapped = ConnectionError("connection failed")
    wrapped.__cause__ = Exception("certificate verify failed")
    assert classify(wrapped) is ErrorCategory.CERTIFICATE

    assert classify(Exception("Cannot connect to proxy")) is ErrorCategory.PROXY
    assert classify(TimeoutError("request timed out")) is ErrorCategory.TIMEOUT
    assert classify(Exception("connection reset by peer")) is ErrorCategory.CONNECTION
    assert classify(Exception("connection reset by peer")).retryable is True
    assert classify(Exception("something odd")) is ErrorCategory.UNKNOWN
    assert classify(Exception("something odd")).retryable is False


def test_each_category_carries_actionable_advice():
    assert "proxy" in ErrorCategory.TIMEOUT.message.lower()
    assert "windows certificate store" in ErrorCategory.CERTIFICATE.message.lower()
    assert "proxy settings" in ErrorCategory.PROXY.message.lower()
    assert "api key" in ErrorCategory.AUTH.message.lower()


def test_backoff_is_exponential_capped_and_honors_retry_after():
    policy = RetryPolicy(max_attempts=5, base_delay=1.0, max_delay=8.0, jitter=0.0)
    assert policy.delay_for(1) == 1.0
    assert policy.delay_for(2) == 2.0
    assert policy.delay_for(3) == 4.0
    assert policy.delay_for(9) == 8.0

    throttled = FakeApiError(429, {"retry-after": "7"})
    assert retry_after_seconds(throttled) == 7.0
    assert policy.delay_for(1, throttled) == 7.0
    # A server value beyond the cap is still capped.
    assert policy.delay_for(1, FakeApiError(429, {"retry-after": "600"})) == 8.0

    three = RetryPolicy(max_attempts=3)
    assert three.should_retry(FakeApiError(429), 1) is True
    assert three.should_retry(FakeApiError(429), 2) is True
    assert three.should_retry(FakeApiError(429), 3) is False


def test_run_with_retry_retries_transient_failures_only():
    attempts: list[int] = []
    delays: list[float] = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise FakeApiError(500)
        return "done"

    result, used = run_with_retry(
        flaky,
        RetryPolicy(max_attempts=3, base_delay=0.0, jitter=0.0),
        sleep=delays.append,
    )
    assert (result, used) == ("done", 3)
    assert len(delays) == 2

    calls: list[int] = []

    def rejected():
        calls.append(1)
        raise FakeApiError(400)

    with pytest.raises(FakeApiError):
        run_with_retry(rejected, RetryPolicy(max_attempts=3), sleep=lambda _s: None)
    assert len(calls) == 1
