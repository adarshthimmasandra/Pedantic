"""Hotkey-to-copy-to-API-to-paste orchestration."""

from __future__ import annotations

import dataclasses

import pytest

from clipai import prompts, usage
from clipai.app import MSG_COPY_FAILED, PedanticApp
from clipai.backends.base import BackendError
from clipai.config import default_config
from clipai.history import History


def grammar(config=None):
    profile = (config or default_config()).profile_by_name("grammar")
    assert profile is not None
    return profile


def test_the_happy_path_copies_transforms_pastes_and_restores(
    make_app, fake_clipboard, fake_keys, fake_backend, isolated_home
):
    app = make_app(history=History())
    outcome = app.run_profile(grammar())

    assert outcome.ok is True
    assert outcome.result == "Hello."
    # Modifiers are released before the copy, and the paste follows it.
    assert fake_keys.events == ["wait", "ctrl+c", "ctrl+v"]
    # The result is written to the clipboard, then the original is put back.
    assert fake_clipboard.writes == ["Hello."]
    assert fake_clipboard.restored == ["original"]
    assert outcome.pasted is True
    assert outcome.restored is True

    # The captured text is sanitized and wrapped as data, not instructions.
    request = fake_backend.requests[0]
    assert request.text == prompts.wrap_user_text("hello")
    assert "Fix spelling, grammar, and punctuation only." in request.system_prompt
    assert request.temperature == 0.1
    assert request.model == "claude-haiku-4-5-20251001"
    assert 0 < request.max_tokens <= 2048

    # History and usage both recorded the request.
    assert app.history is not None
    entries = app.history.load()
    assert len(entries) == 1
    assert entries[0].profile == "grammar"
    assert entries[0].result == "Hello."
    assert usage.month_summary().requests == 1


def test_a_failed_copy_reports_the_documented_message_and_skips_the_api(
    make_app, fake_clipboard, fake_backend, notifier, isolated_home
):
    # No clipboard change means no usable selection was copied.
    fake_clipboard.copy_result = None
    app = make_app(history=History())

    outcome = app.run_profile(grammar())

    assert outcome.ok is False
    assert outcome.message == MSG_COPY_FAILED
    assert "Could not copy selected text" in outcome.message
    assert fake_backend.requests == []
    assert notifier.levels == ["error"]
    assert usage.month_summary().requests == 0


def test_requests_that_cannot_be_served_are_refused_before_the_api(
    make_app, fake_clipboard, fake_backend, isolated_home
):
    # Whitespace and punctuation are not worth a request.
    fake_clipboard.copy_result = "  \n -- \n "
    app = make_app(history=History())
    outcome = app.run_profile(grammar())
    assert outcome.ok is False
    assert "No text was selected" in outcome.message
    assert fake_clipboard.restored == ["original"]

    # Oversized selections are refused rather than truncated, because a partial
    # rewrite would silently destroy the rest of the user's text.
    config = default_config()
    small = dataclasses.replace(
        config, api=dataclasses.replace(config.api, max_input_chars=100)
    )
    fake_clipboard.copy_result = "word " * 200
    long_outcome = make_app(config=small, history=History()).run_profile(grammar(small))
    assert long_outcome.ok is False
    assert "too long" in long_outcome.message

    # A reached budget stops the transformation before the clipboard is touched.
    usage.record_usage("grammar", "claude-haiku-4-5-20251001", 1_000_000, 0)
    limited = dataclasses.replace(
        config, budget=dataclasses.replace(config.budget, monthly_usd_limit=0.01)
    )
    fake_clipboard.copy_calls = 0
    budget_outcome = make_app(config=limited, history=History()).run_profile(
        grammar(limited)
    )
    assert budget_outcome.ok is False
    assert "Monthly budget reached" in budget_outcome.message
    assert fake_clipboard.copy_calls == 0
    assert fake_backend.requests == []


def test_api_failures_restore_the_clipboard_and_report_the_reason(
    make_app, make_backend, fake_clipboard, notifier, monkeypatch, isolated_home
):
    failing = make_backend(
        error=BackendError("Proxy failure. Check your Windows proxy settings.")
    )
    outcome = make_app(backend=failing, history=History()).run_profile(grammar())

    assert outcome.ok is False
    assert "Proxy failure" in outcome.message
    # The user's clipboard must survive a failed transformation.
    assert fake_clipboard.restored == ["original"]
    assert fake_clipboard.writes == []
    assert notifier.levels == ["error"]
    assert usage.month_summary().requests == 0

    # A missing key is reported the same way rather than crashing the listener.
    from clipai import app as app_module

    monkeypatch.setattr(app_module, "get_api_key", lambda: None)
    keyless = make_app(backend=None, history=History())
    with pytest.raises(BackendError, match="No Anthropic API key"):
        keyless.get_backend()
    keyless_outcome = keyless.run_profile(grammar())
    assert keyless_outcome.ok is False
    assert "No Anthropic API key" in keyless_outcome.message


def test_paste_and_restore_flags_are_honored(
    make_app, fake_clipboard, fake_keys, isolated_home
):
    config = default_config()
    no_paste = dataclasses.replace(
        config, profiles=(dataclasses.replace(grammar(config), paste=False),)
    )
    outcome = make_app(config=no_paste, history=History()).run_profile(
        no_paste.profiles[0]
    )

    assert outcome.ok is True
    assert outcome.pasted is False
    assert "ctrl+v" not in fake_keys.events
    # The result stays on the clipboard, so the original is not restored.
    assert fake_clipboard.writes == ["Hello."]
    assert fake_clipboard.restored == []

    keep = dataclasses.replace(
        config, behavior=dataclasses.replace(config.behavior, restore_clipboard=False)
    )
    keep_outcome = make_app(config=keep, history=History()).run_profile(grammar(keep))
    assert keep_outcome.pasted is True
    assert keep_outcome.restored is False
    assert fake_clipboard.restored == []


def test_only_one_transformation_runs_at_a_time(make_app, isolated_home):
    app = make_app(history=History())

    # Simulate a second hotkey press arriving while the first is in flight.
    assert app._lock.acquire(blocking=False) is True
    try:
        outcome = app.run_profile(grammar())
    finally:
        app._lock.release()

    assert outcome.ok is False
    assert "already running" in outcome.message


def test_reloaded_configuration_rebinds_hotkeys_and_drops_the_backend(
    make_app, fake_backend, isolated_home
):
    from clipai.hotkeys import HotkeyListener

    app = make_app(history=History())
    listener = HotkeyListener(callback=app.handle_hotkey, bindings=app.bindings())
    app.listener = listener
    assert app.bindings()["ctrl+shift+g"] == "grammar"

    config = default_config()
    rebound = dataclasses.replace(
        config, profiles=(dataclasses.replace(grammar(config), hotkey="ctrl+alt+p"),)
    )
    app.apply_config(rebound)

    assert listener.bindings == {"ctrl+alt+p": "grammar"}
    # Dropping the backend makes a changed model or timeout take effect.
    assert fake_backend.closed is True
    assert app.backend is None
    # An unbound hotkey is ignored rather than treated as an error.
    assert app.handle_hotkey("ctrl+shift+g") is None


def test_notifications_respect_the_configured_levels(
    make_app, notifier, isolated_home
):
    config = default_config()
    quiet = dataclasses.replace(
        config,
        behavior=dataclasses.replace(
            config.behavior, notify_on_success=False, notify_on_error=False
        ),
    )
    quiet_app = make_app(config=quiet, history=History())
    quiet_app.notify("Pedantic", "worked", "success")
    quiet_app.notify("Pedantic", "broke", "error")
    assert notifier.messages == []

    loud = dataclasses.replace(
        config,
        behavior=dataclasses.replace(
            config.behavior, notify_on_success=True, notify_on_error=True
        ),
    )
    loud_app = make_app(config=loud, history=History())
    loud_app.notify("Pedantic", "worked", "success")
    loud_app.notify("Pedantic", "broke", "error")
    assert notifier.levels == ["success", "error"]
