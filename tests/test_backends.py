"""Backend interface, output sizing, and the Anthropic client helpers."""

from __future__ import annotations

import inspect
import ssl

import pytest

from clipai.backends import anthropic_api
from clipai.backends.base import (
    MIN_OUTPUT_TOKENS,
    BackendError,
    TransformRequest,
    estimate_tokens,
    size_max_tokens,
)
from clipai.backends.retry import RetryPolicy


class FakeBlock:
    def __init__(self, text: str, type: str = "text"):
        self.text = text
        self.type = type


class FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeMessage:
    def __init__(self, blocks, model="claude-haiku-4-5-20251001", usage=None):
        self.content = blocks
        self.model = model
        self.usage = usage


class FakeMessages:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, response=None, error=None):
        self.messages = FakeMessages(response, error)
        self.closed = False

    def close(self):
        self.closed = True


def request(text: str = "x", max_tokens: int = 256) -> TransformRequest:
    return TransformRequest(
        text=text,
        system_prompt="rules",
        temperature=0.1,
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
    )


def test_the_output_budget_scales_with_input_within_bounds():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 400) == 100

    # Small inputs still get a usable floor, large ones stop at the ceiling.
    assert size_max_tokens("short", 2048) == MIN_OUTPUT_TOKENS
    assert size_max_tokens("word " * 2000, 2048) == 2048
    assert MIN_OUTPUT_TOKENS < size_max_tokens("x" * 4000, 8000) <= 8000

    with pytest.raises(ValueError):
        size_max_tokens("text", 0)


def test_response_text_and_usage_are_extracted_from_the_message():
    message = FakeMessage(
        [FakeBlock("Hello "), FakeBlock("world."), FakeBlock("drop", type="thinking")],
        usage=FakeUsage(9, 4),
    )
    assert anthropic_api.extract_text(message) == "Hello world."
    assert anthropic_api.extract_usage(message) == (9, 4)
    assert anthropic_api.extract_usage(FakeMessage([], usage=None)) == (0, 0)


def test_the_ssl_context_trusts_certificates_and_verifies_hostnames():
    context = anthropic_api.build_ssl_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    # certifi alone contributes well over a hundred authorities.
    assert len(context.get_ca_certs()) > 20


def test_proxy_selection_prefers_https_and_normalizes_the_scheme():
    assert anthropic_api.https_proxy_url({"https": "http://p:8080"}) == "http://p:8080"
    assert anthropic_api.https_proxy_url({"http": "http://p:8080"}) == "http://p:8080"
    assert anthropic_api.https_proxy_url({"https": "proxy:3128"}) == "http://proxy:3128"
    assert anthropic_api.https_proxy_url({}) is None


def test_the_backend_sends_the_configured_request():
    client = FakeClient(FakeMessage([FakeBlock("Fixed.")], usage=FakeUsage(12, 3)))
    backend = anthropic_api.AnthropicBackend(api_key="sk-ant-test", client=client)

    result = backend.transform(request("teh", max_tokens=300))

    call = client.messages.calls[0]
    assert call["model"] == "claude-haiku-4-5-20251001"
    assert call["max_tokens"] == 300
    assert call["system"] == "rules"
    assert call["messages"] == [{"role": "user", "content": "teh"}]
    assert result.text == "Fixed."
    assert (result.input_tokens, result.output_tokens) == (12, 3)
    assert result.total_tokens == 15

    # Every keyword must exist on the installed SDK. Binding against the real
    # signature is what catches a parameter being removed upstream, which is
    # otherwise invisible until the first hotkey press fails.
    from anthropic.resources.messages import Messages

    inspect.signature(Messages.create).bind(None, **call)

    # The 1.x SDK removed the typed temperature parameter while the HTTP API
    # kept honoring it, so the value travels in the request body instead.
    def modern(*, model, max_tokens, messages, system=None, extra_body=None):
        raise AssertionError("not called")

    modern_kwargs = anthropic_api.build_create_kwargs(request(), modern)
    assert modern_kwargs["extra_body"] == {"temperature": 0.1}
    assert "temperature" not in modern_kwargs

    # An older SDK that still exposes it gets a plain keyword.
    def legacy(*, model, max_tokens, messages, system=None, temperature=None):
        raise AssertionError("not called")

    legacy_kwargs = anthropic_api.build_create_kwargs(request(), legacy)
    assert legacy_kwargs["temperature"] == 0.1
    assert "extra_body" not in legacy_kwargs

    # Both forms must be acceptable to the function they were built for.
    inspect.signature(modern).bind(**modern_kwargs)
    inspect.signature(legacy).bind(**legacy_kwargs)


def test_failures_become_actionable_backend_errors():
    empty = anthropic_api.AnthropicBackend(
        api_key="sk-ant-test", client=FakeClient(FakeMessage([FakeBlock("   ")]))
    )
    with pytest.raises(BackendError, match="empty"):
        empty.transform(request())

    timing_out = anthropic_api.AnthropicBackend(
        api_key="sk-ant-test",
        client=FakeClient(error=TimeoutError("request timed out")),
        policy=RetryPolicy(max_attempts=1),
    )
    with pytest.raises(BackendError, match="timed out"):
        timing_out.transform(request())

    class Rejected(Exception):
        status_code = 401

    rejected = anthropic_api.AnthropicBackend(
        api_key="sk-ant-test", client=FakeClient(error=Rejected())
    )
    assert rejected.validate_credentials() is False

    with pytest.raises(BackendError, match="no Anthropic API key"):
        anthropic_api.AnthropicBackend(api_key="")

    client = FakeClient(FakeMessage([FakeBlock("ok")]))
    anthropic_api.AnthropicBackend(api_key="sk-ant-test", client=client).close()
    assert client.closed is True
