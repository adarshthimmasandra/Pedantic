"""Tests that touch the real system or the paid API.

These are deselected by default because they drive the real clipboard and
keyboard, or spend money. Run them explicitly:

.. code-block:: powershell

    python -m pytest -m integration
    python -m pytest -m live_api
"""

from __future__ import annotations

import sys

import pytest

from clipai import cleaning
from clipai.config import default_config

windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="requires Windows"
)


@pytest.mark.integration
@windows_only
def test_the_real_clipboard_round_trips_text_and_restores_it():
    from clipai import clipboard

    before = clipboard.snapshot()
    try:
        clipboard.write_text("Pedantic integration test")
        assert clipboard.read_text() == "Pedantic integration test"
        assert clipboard.get_sequence_number() != before.sequence
    finally:
        clipboard.restore(before)


@pytest.mark.integration
@windows_only
def test_the_hotkey_listener_installs_and_removes_its_hook():
    from clipai.hotkeys import HotkeyListener

    received: list[str] = []
    listener = HotkeyListener(
        callback=received.append, bindings={"ctrl+alt+f24": "grammar"}
    )
    listener.start()
    try:
        assert listener.matches("ctrl+alt+f24") == "grammar"
    finally:
        listener.stop()


@pytest.mark.live_api
def test_a_live_grammar_transformation_returns_corrected_text():
    from clipai.app import PedanticApp
    from clipai.credentials import get_api_key

    api_key = get_api_key()
    if not api_key:
        pytest.skip("no Anthropic API key is configured")

    config = default_config()
    profile = config.profile_by_name("grammar")
    assert profile is not None

    app = PedanticApp(config=config)
    result = app._call_backend(profile, "she dont has no time", config)
    cleaned = cleaning.clean_output(result.text)

    assert cleaned
    assert "dont" not in cleaned.lower()
    assert result.output_tokens > 0
