"""Hotkey parsing, formatting, and event matching."""

from __future__ import annotations

import pytest

from clipai import hotkeys
from clipai.hotkeys import (
    HotkeyError,
    HotkeyListener,
    format_hotkey,
    hotkey_from_event,
    normalize_hotkey,
    parse_hotkey,
)


def test_hotkeys_are_normalized_to_a_canonical_form():
    assert normalize_hotkey("Shift+Ctrl+G") == "ctrl+shift+g"
    assert normalize_hotkey("WIN + alt + ctrl + shift + f9") == "ctrl+alt+shift+win+f9"
    assert normalize_hotkey("control + shift + g") == "ctrl+shift+g"
    assert normalize_hotkey("ctrl+alt+escape") == "ctrl+alt+esc"
    assert normalize_hotkey("super+alt+return") == "alt+win+enter"


def test_parse_splits_modifiers_from_the_key():
    modifiers, key = parse_hotkey("ctrl+shift+g")
    assert modifiers == {"ctrl", "shift"}
    assert key == "g"


def test_unsafe_or_malformed_hotkeys_are_rejected():
    # Shift+G alone would fire whenever the user types a capital G.
    with pytest.raises(HotkeyError, match="ctrl, alt, or win"):
        normalize_hotkey("shift+g")
    with pytest.raises(HotkeyError, match="unknown key"):
        normalize_hotkey("ctrl+shift+nope")
    with pytest.raises(HotkeyError, match="only contain one"):
        normalize_hotkey("ctrl+g+h")
    with pytest.raises(HotkeyError, match="needs a non-modifier key"):
        normalize_hotkey("ctrl+shift")
    with pytest.raises(HotkeyError):
        normalize_hotkey("")


def test_format_hotkey_is_human_readable():
    assert format_hotkey("ctrl+shift+g") == "Ctrl+Shift+G"
    assert format_hotkey("ctrl+alt+f12") == "Ctrl+Alt+F12"
    assert format_hotkey("ctrl+alt+delete") == "Ctrl+Alt+Delete"


def test_events_map_to_hotkeys_only_with_a_real_modifier():
    assert hotkey_from_event(0x47, frozenset({"ctrl", "shift"})) == "ctrl+shift+g"
    # Modifier key-downs, unmodified keys, and unmapped codes are not hotkeys.
    assert hotkey_from_event(hotkeys.VK_CONTROL, frozenset({"ctrl"})) is None
    assert hotkey_from_event(0x47, frozenset({"shift"})) is None
    assert hotkey_from_event(0x01, frozenset({"ctrl"})) is None


def test_listener_bindings_can_be_replaced_while_running():
    listener = HotkeyListener(
        callback=lambda _hotkey: None, bindings={"ctrl+shift+g": "grammar"}
    )
    assert listener.matches("ctrl+shift+g") == "grammar"
    assert listener.matches("ctrl+shift+q") is None
    assert listener.matches(None) is None

    listener.set_bindings({"ctrl+alt+p": "formal"})
    assert listener.bindings == {"ctrl+alt+p": "formal"}
    assert listener.matches("ctrl+shift+g") is None
