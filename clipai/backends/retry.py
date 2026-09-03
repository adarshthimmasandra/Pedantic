"""Retry policy and error classification.

Failures are sorted into categories because the useful advice differs sharply:
a certificate error needs a Windows trust-store change, a proxy error needs
Windows proxy settings, and a 401 needs a new API key. Telling a user behind a
TLS-inspecting corporate proxy to "check the network" wastes their time.

Only failures that can plausibly succeed on a second attempt are retried:
timeouts, connection drops, rate limits, and server errors. A 400 or a 401 will
fail identically every time, so retrying only delays the error message.
"""

from __future__ import annotations

import enum
import logging
import random
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 529})


class ErrorCategory(enum.Enum):
    """Why a request failed, in terms the user can act on."""

    TIMEOUT = "timeout"
    CERTIFICATE = "certificate"
    PROXY = "proxy"
    CONNECTION = "connection"
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    SERVER = "server"
    REQUEST = "request"
    OVERLOADED = "overloaded"
    UNKNOWN = "unknown"

    @property
    def retryable(self) -> bool:
        return self in _RETRYABLE_CATEGORIES

    @property
    def message(self) -> str:
        return _MESSAGES[self]


_RETRYABLE_CATEGORIES = frozenset(
    {
        ErrorCategory.TIMEOUT,
        ErrorCategory.CONNECTION,
        ErrorCategory.RATE_LIMIT,
        ErrorCategory.SERVER,
        ErrorCategory.OVERLOADED,
    }
)

_MESSAGES = {
    ErrorCategory.TIMEOUT: (
        "AI request timed out. Check your network or proxy and try again."
    ),
    ErrorCategory.CERTIFICATE: (
        "Certificate failure. Your organization's certificate must be trusted "
        "in the Windows certificate store."
    ),
    ErrorCategory.PROXY: "Proxy failure. Check your Windows proxy settings.",
    ErrorCategory.CONNECTION: (
        "Could not reach the Anthropic API. Check your network connection."
    ),
    ErrorCategory.AUTH: "API key rejected. Update the key from the tray menu.",
    ErrorCategory.RATE_LIMIT: "Rate limited by Anthropic. Try again shortly.",
    ErrorCategory.SERVER: "Anthropic returned a server error. Try again shortly.",
    ErrorCategory.REQUEST: "The AI request was rejected as invalid.",
    ErrorCategory.OVERLOADED: "Anthropic is overloaded. Try again shortly.",
    ErrorCategory.UNKNOWN: "The AI request failed.",
}

_CERTIFICATE_HINTS = (
    "certificate verify failed",
    "certificate_verify_failed",
    "sslcertverificationerror",
    "self signed certificate",
    "self-signed certificate",
    "unable to get local issuer",
    "ssl: ",
)

_PROXY_HINTS = (
    "proxy",
    "407",
    "tunnel connection failed",
    "cannot connect to proxy",
)

_TIMEOUT_HINTS = ("timeout", "timed out")

_CONNECTION_HINTS = (
    "connection refused",
    "connection reset",
    "connection aborted",
    "connection error",
    "name or service not known",
    "temporary failure in name resolution",
    "getaddrinfo failed",
    "network is unreachable",
    "remote end closed",
    "server disconnected",
)


def status_code_of(error: BaseException) -> int | None:
    """Extract an HTTP status code from an exception, if it carries one."""
    for attribute in ("status_code", "status"):
        value = getattr(error, attribute, None)
        if isinstance(value, int):
            return value
    response = getattr(error, "response", None)
    value = getattr(response, "status_code", None)
    if isinstance(value, int):
        return value
    return None


def category_for_status(status: int) -> ErrorCategory:
    """Map an HTTP status code to a category."""
    if status == 401 or status == 403:
        return ErrorCategory.AUTH
    if status == 429:
        return ErrorCategory.RATE_LIMIT
    if status == 529:
        return ErrorCategory.OVERLOADED
    if status == 407:
        return ErrorCategory.PROXY
    if status == 408:
        return ErrorCategory.TIMEOUT
    if 500 <= status <= 599:
        return ErrorCategory.SERVER
    if 400 <= status <= 499:
        return ErrorCategory.REQUEST
    return ErrorCategory.UNKNOWN


def classify(error: BaseException) -> ErrorCategory:
    """Classify a transport or API exception.

    Status codes win when present because they are unambiguous. Otherwise the
    exception chain is inspected by type name and message, which is how a
    certificate failure buried under a generic connection error is found.
    """
    status = status_code_of(error)
    if status is not None:
        return category_for_status(status)

    # The whole chain is inspected for each category in turn rather than
    # classifying the outermost exception first. httpx wraps a certificate
    # failure in a generic connection error, and reporting that as "check your
    # network" would send the user looking in the wrong place.
    chain: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(f"{type(current).__name__} {current}".lower())
        current = current.__cause__ or current.__context__

    def matches(type_hints: tuple[str, ...], message_hints: tuple[str, ...]) -> bool:
        return any(
            any(hint in text for hint in type_hints + message_hints) for text in chain
        )

    if matches(("certificate", "sslerror"), _CERTIFICATE_HINTS):
        return ErrorCategory.CERTIFICATE
    if matches(("proxy",), _PROXY_HINTS):
        return ErrorCategory.PROXY
    if matches(("timeout",), _TIMEOUT_HINTS):
        return ErrorCategory.TIMEOUT
    if matches(("connect",), _CONNECTION_HINTS):
        return ErrorCategory.CONNECTION
    return ErrorCategory.UNKNOWN


def retry_after_seconds(error: BaseException) -> float | None:
    """Read a ``Retry-After`` header from an exception's response, if present."""
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        raw = headers.get("retry-after") or headers.get("Retry-After")
    except Exception:
        return None
    if raw is None:
        return None
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    return value


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff with jitter, capped and ``Retry-After`` aware."""

    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 20.0
    jitter: float = 0.25

    def should_retry(self, error: BaseException, attempt: int) -> bool:
        """True when ``attempt`` (1-based) may be followed by another try."""
        if attempt >= self.max_attempts:
            return False
        return classify(error).retryable

    def delay_for(self, attempt: int, error: BaseException | None = None) -> float:
        """Seconds to wait after a failed ``attempt``.

        A server-supplied ``Retry-After`` is honored over the computed backoff,
        because the server knows when its rate-limit window resets.
        """
        if error is not None:
            supplied = retry_after_seconds(error)
            if supplied is not None:
                return min(supplied, self.max_delay)
        delay = min(self.base_delay * (2 ** max(attempt - 1, 0)), self.max_delay)
        if self.jitter:
            spread = delay * self.jitter
            delay = max(0.0, delay + random.uniform(-spread, spread))
        return delay


def run_with_retry(
    operation: Callable[[], T],
    policy: RetryPolicy,
    *,
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Callable[[int, float, BaseException], None] | None = None,
) -> tuple[T, int]:
    """Run ``operation`` under ``policy``.

    Returns the result and the number of attempts used. The last exception is
    re-raised when every attempt fails.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return operation(), attempt
        except BaseException as error:  # noqa: BLE001 - re-raised below
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            if not policy.should_retry(error, attempt):
                raise
            delay = policy.delay_for(attempt, error)
            log.warning(
                "attempt %d/%d failed (%s); retrying in %.1fs",
                attempt,
                policy.max_attempts,
                classify(error).value,
                delay,
            )
            if on_retry is not None:
                on_retry(attempt, delay, error)
            sleep(delay)
