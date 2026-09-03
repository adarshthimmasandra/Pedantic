"""Anthropic client and Windows network configuration.

Pedantic runs on managed Windows desktops, where two things routinely break a
default HTTPS client.

*TLS inspection.* Corporate proxies re-sign traffic with an internal authority
that is installed in the Windows certificate stores and is absent from the
``certifi`` bundle Python ships. :func:`build_ssl_context` therefore trusts
``certifi`` **and** the Windows ``ROOT`` and ``CA`` stores, the latter being
where organization-deployed authorities land.

*Proxies.* Windows proxy configuration lives in the registry, not in
environment variables. :func:`detect_proxies` reads it through Python's
Windows-aware discovery, and a detected HTTPS proxy is mounted explicitly so it
takes priority while environment proxies keep working as a fallback.

Retries are handled by :mod:`clipai.backends.retry` rather than by the SDK, so
that one policy governs error classification, backoff, and ``Retry-After``.
"""

from __future__ import annotations

import inspect
import logging
import ssl
import sys
from typing import Any, Iterable

import certifi

from ..prompts import unwrap_user_text
from .base import Backend, BackendError, TransformRequest, TransformResult
from .retry import ErrorCategory, RetryPolicy, classify, run_with_retry

log = logging.getLogger(__name__)

WINDOWS_CERT_STORES = ("ROOT", "CA")
SERVER_AUTH_OID = "1.3.6.1.5.5.7.3.1"
API_BASE_URL = "https://api.anthropic.com"
VALIDATION_MAX_TOKENS = 16


def _windows_root_certificates(stores: Iterable[str] = WINDOWS_CERT_STORES) -> list[str]:
    """Collect PEM certificates trusted for server authentication by Windows."""
    if sys.platform != "win32":
        return []
    collected: list[str] = []
    for store in stores:
        try:
            entries = ssl.enum_certificates(store)
        except Exception:
            log.debug("could not read the Windows %s certificate store", store)
            continue
        for cert_bytes, encoding, trust in entries:
            if encoding != "x509_asn":
                continue
            trusted = trust is True or (
                isinstance(trust, (set, frozenset, tuple, list))
                and SERVER_AUTH_OID in trust
            )
            if not trusted:
                continue
            try:
                collected.append(ssl.DER_cert_to_PEM_cert(cert_bytes))
            except Exception:
                continue
    log.debug("collected %d certificates from the Windows stores", len(collected))
    return collected


def build_ssl_context() -> ssl.SSLContext:
    """Create an SSL context trusting certifi plus the Windows trust stores."""
    context = ssl.create_default_context(cafile=certifi.where())
    loaded = 0
    for pem in _windows_root_certificates():
        try:
            context.load_verify_locations(cadata=pem)
            loaded += 1
        except ssl.SSLError:
            # Expired or malformed entries are common in real stores; skip them.
            continue
    if loaded:
        log.debug("added %d Windows certificates to the SSL context", loaded)
    return context


def detect_proxies() -> dict[str, str]:
    """Return proxy settings from Windows or the environment."""
    import urllib.request

    try:
        discovered = urllib.request.getproxies()
    except Exception:
        log.debug("proxy discovery failed", exc_info=True)
        return {}
    return {
        scheme: url
        for scheme, url in discovered.items()
        if scheme in {"http", "https"} and url
    }


def https_proxy_url(proxies: dict[str, str] | None = None) -> str | None:
    """Pick the proxy that should carry HTTPS traffic."""
    resolved = detect_proxies() if proxies is None else proxies
    url = resolved.get("https") or resolved.get("http")
    if not url:
        return None
    if "://" not in url:
        url = f"http://{url}"
    return url


def _import_httpx():
    """Import the httpx implementation the installed SDK is built on."""
    try:
        import httpx2 as httpx  # httpx 2.x is published under this name
    except ImportError:  # pragma: no cover - fallback for httpx 0.x/1.x installs
        import httpx  # type: ignore[no-redef]
    return httpx


def build_http_client(timeout: float) -> Any:
    """Create the HTTP client used by the Anthropic SDK."""
    httpx = _import_httpx()
    context = build_ssl_context()
    proxy = https_proxy_url()
    kwargs: dict[str, Any] = {"verify": context, "timeout": timeout}
    if proxy:
        # Mounting the transport explicitly wins over environment discovery,
        # which is what makes an registry-configured proxy take effect.
        log.info("using HTTPS proxy %s", proxy)
        kwargs["mounts"] = {
            "https://": httpx.HTTPTransport(verify=context, proxy=proxy),
        }
    try:
        return httpx.Client(**kwargs)
    except TypeError:
        kwargs.pop("mounts", None)
        client = httpx.Client(**kwargs)
        if proxy:
            log.warning("this httpx version does not support transport mounts")
        return client


def create_client(api_key: str, timeout: float) -> Any:
    """Create an Anthropic client configured for this machine.

    SDK-level retries are disabled because :mod:`clipai.backends.retry` owns
    retry behavior; leaving both enabled would multiply the attempt count.
    """
    import anthropic

    return anthropic.Anthropic(
        api_key=api_key,
        timeout=timeout,
        max_retries=0,
        http_client=build_http_client(timeout),
    )


def accepts_temperature_keyword(create: Any) -> bool:
    """True when the installed SDK exposes ``temperature`` as a parameter.

    The 0.x SDK took ``temperature`` directly. The 1.x SDK removed it from the
    generated Messages API surface even though the HTTP API still honors it, so
    the value has to travel in the request body instead. Deciding by
    introspection keeps one code path working across both.
    """
    try:
        return "temperature" in inspect.signature(create).parameters
    except (TypeError, ValueError):
        return False


def build_create_kwargs(request: TransformRequest, create: Any) -> dict[str, Any]:
    """Assemble the keyword arguments for one Messages API call."""
    kwargs: dict[str, Any] = {
        "model": request.model,
        "max_tokens": request.max_tokens,
        "system": request.system_prompt,
        "messages": [{"role": "user", "content": request.text}],
    }
    if accepts_temperature_keyword(create):
        kwargs["temperature"] = request.temperature
    else:
        kwargs["extra_body"] = {"temperature": request.temperature}
    return kwargs


def extract_text(message: Any) -> str:
    """Pull the text out of a Messages API response."""
    blocks = getattr(message, "content", None) or []
    parts: list[str] = []
    for block in blocks:
        if getattr(block, "type", None) not in (None, "text"):
            continue
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text:
            parts.append(str(text))
    return "".join(parts)


def extract_usage(message: Any) -> tuple[int, int]:
    """Pull input and output token counts out of a response."""
    usage = getattr(message, "usage", None)
    if usage is None:
        return 0, 0
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    return int(input_tokens), int(output_tokens)


class AnthropicBackend(Backend):
    """Production backend backed by the Anthropic Messages API."""

    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        timeout: float = 30.0,
        policy: RetryPolicy | None = None,
        client: Any | None = None,
    ) -> None:
        if not api_key:
            raise BackendError("no Anthropic API key is configured")
        self._api_key = api_key
        self._timeout = timeout
        self._policy = policy or RetryPolicy()
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = create_client(self._api_key, self._timeout)
        return self._client

    def transform(self, request: TransformRequest) -> TransformResult:
        create = self.client.messages.create
        kwargs = build_create_kwargs(request, create)

        def call() -> Any:
            return create(**kwargs)

        try:
            message, attempts = run_with_retry(call, self._policy)
        except BackendError:
            raise
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            category = classify(error)
            log.error("Anthropic request failed (%s): %s", category.value, error)
            raise BackendError(
                category.message, retryable=category.retryable
            ) from error

        text = unwrap_user_text(extract_text(message))
        if not text.strip():
            raise BackendError("The AI returned an empty response.")
        input_tokens, output_tokens = extract_usage(message)
        return TransformResult(
            text=text,
            model=getattr(message, "model", request.model),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            attempts=attempts,
        )

    def validate_credentials(self) -> bool:
        """Send the smallest possible request to prove the key works."""
        try:
            self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=VALIDATION_MAX_TOKENS,
                messages=[{"role": "user", "content": "ping"}],
            )
            return True
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            category = classify(error)
            if category is ErrorCategory.AUTH:
                return False
            raise BackendError(category.message, retryable=category.retryable) from error

    def close(self) -> None:
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            client.close()
        except Exception:
            log.debug("could not close the Anthropic client", exc_info=True)
