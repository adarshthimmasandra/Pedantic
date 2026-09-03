"""Global hotkey listener and Windows suppression.

Hotkey strings are normalized into a canonical form (``ctrl+shift+g``) so that
configuration, logs, and the tray menu all agree, and so duplicate bindings can
be detected regardless of how the user typed them.

Detection uses a Windows low-level keyboard hook through ``pynput``. When a bound
combination is seen the event is suppressed, which is what keeps the foreground
application from also acting on it -- pressing ``Ctrl+Shift+C`` in a terminal
must not send an interrupt, and ``Ctrl+Shift+B`` must not toggle bold in Word.

The hook callback must return quickly. Windows silently unhooks a hook that
exceeds ``LowLevelHooksTimeout``, so the callback only enqueues the hotkey and a
dedicated dispatch thread runs the application logic.
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
from typing import Callable, Mapping

from .keys import is_injected_by_us

log = logging.getLogger(__name__)


class HotkeyError(Exception):
    """Raised for a hotkey string that cannot be parsed or bound."""


MODIFIER_ORDER = ("ctrl", "alt", "shift", "win")

_MODIFIER_ALIASES = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "ctl": "ctrl",
    "alt": "alt",
    "menu": "alt",
    "option": "alt",
    "shift": "shift",
    "win": "win",
    "super": "win",
    "cmd": "win",
    "meta": "win",
    "windows": "win",
}

_KEY_ALIASES = {
    "escape": "esc",
    "return": "enter",
    "spacebar": "space",
    "pgup": "pageup",
    "page_up": "pageup",
    "pgdn": "pagedown",
    "pagedn": "pagedown",
    "page_down": "pagedown",
    "del": "delete",
    "ins": "insert",
    "backspace": "backspace",
    "bksp": "backspace",
    "plus": "=",
    "minus": "-",
    "comma": ",",
    "period": ".",
    "slash": "/",
    "backslash": "\\",
    "semicolon": ";",
    "quote": "'",
    "grave": "`",
    "tilde": "`",
}

# Virtual-key codes mapped to the canonical key names used in hotkey strings.
_VK_TO_NAME: dict[int, str] = {
    0x08: "backspace",
    0x09: "tab",
    0x0D: "enter",
    0x1B: "esc",
    0x20: "space",
    0x21: "pageup",
    0x22: "pagedown",
    0x23: "end",
    0x24: "home",
    0x25: "left",
    0x26: "up",
    0x27: "right",
    0x28: "down",
    0x2D: "insert",
    0x2E: "delete",
    0xBA: ";",
    0xBB: "=",
    0xBC: ",",
    0xBD: "-",
    0xBE: ".",
    0xBF: "/",
    0xC0: "`",
    0xDB: "[",
    0xDC: "\\",
    0xDD: "]",
    0xDE: "'",
}
for _code in range(0x30, 0x3A):  # 0-9
    _VK_TO_NAME[_code] = chr(_code)
for _code in range(0x41, 0x5B):  # A-Z
    _VK_TO_NAME[_code] = chr(_code).lower()
for _index in range(1, 25):  # F1-F24
    _VK_TO_NAME[0x6F + _index] = f"f{_index}"
for _index in range(10):  # numpad
    _VK_TO_NAME[0x60 + _index] = f"num{_index}"

_NAME_TO_VK = {name: code for code, name in _VK_TO_NAME.items()}

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_LWIN = 0x5B
VK_RWIN = 0x5C

_MODIFIER_VKS = {
    VK_SHIFT,
    VK_CONTROL,
    VK_MENU,
    VK_LWIN,
    VK_RWIN,
    0xA0,
    0xA1,  # L/R shift
    0xA2,
    0xA3,  # L/R control
    0xA4,
    0xA5,  # L/R alt
}

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

_KEYDOWN_MESSAGES = frozenset({WM_KEYDOWN, WM_SYSKEYDOWN})
_KEYUP_MESSAGES = frozenset({WM_KEYUP, WM_SYSKEYUP})


def parse_hotkey(spec: str) -> tuple[frozenset[str], str]:
    """Split a hotkey string into its modifiers and its main key.

    Raises :class:`HotkeyError` for an empty spec, an unknown key name, a
    duplicate main key, or a combination without a real modifier.
    """
    if not isinstance(spec, str) or not spec.strip():
        raise HotkeyError("hotkey must be a non-empty string")

    parts = [part.strip().lower() for part in spec.replace("_", "+").split("+")]
    parts = [part for part in parts if part]
    if not parts:
        raise HotkeyError(f"could not parse hotkey: {spec!r}")

    modifiers: set[str] = set()
    key: str | None = None
    for part in parts:
        if part in _MODIFIER_ALIASES:
            modifiers.add(_MODIFIER_ALIASES[part])
            continue
        candidate = _KEY_ALIASES.get(part, part)
        if candidate not in _NAME_TO_VK:
            raise HotkeyError(f"unknown key: {part!r}")
        if key is not None:
            raise HotkeyError(f"hotkey may only contain one non-modifier key: {spec!r}")
        key = candidate

    if key is None:
        raise HotkeyError(f"hotkey needs a non-modifier key: {spec!r}")
    if not modifiers & {"ctrl", "alt", "win"}:
        raise HotkeyError(
            f"hotkey needs ctrl, alt, or win so it cannot collide with typing: {spec!r}"
        )
    return frozenset(modifiers), key


def normalize_hotkey(spec: str) -> str:
    """Return the canonical form of a hotkey string."""
    modifiers, key = parse_hotkey(spec)
    ordered = [name for name in MODIFIER_ORDER if name in modifiers]
    return "+".join([*ordered, key])


def format_hotkey(spec: str) -> str:
    """Human-readable form for menus and notifications, e.g. ``Ctrl+Shift+G``."""
    modifiers, key = parse_hotkey(spec)
    labels = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}
    ordered = [labels[name] for name in MODIFIER_ORDER if name in modifiers]
    if len(key) == 1:
        shown = key.upper()
    elif key.startswith("f") and key[1:].isdigit():
        shown = key.upper()
    else:
        shown = key.capitalize()
    return "+".join([*ordered, shown])


def _async_modifier_state() -> frozenset[str]:
    """Read the physical modifier state directly from Windows.

    Deriving modifiers from tracked key events drifts out of sync whenever a
    key-up is missed, for example while another application had the keyboard
    suppressed. Asking Windows avoids keeping any state at all.
    """
    import ctypes

    user32 = ctypes.windll.user32
    active: set[str] = set()
    if user32.GetAsyncKeyState(VK_CONTROL) & 0x8000:
        active.add("ctrl")
    if user32.GetAsyncKeyState(VK_MENU) & 0x8000:
        active.add("alt")
    if user32.GetAsyncKeyState(VK_SHIFT) & 0x8000:
        active.add("shift")
    if (user32.GetAsyncKeyState(VK_LWIN) & 0x8000) or (
        user32.GetAsyncKeyState(VK_RWIN) & 0x8000
    ):
        active.add("win")
    return frozenset(active)


def hotkey_from_event(vk_code: int, modifiers: frozenset[str]) -> str | None:
    """Build a canonical hotkey string for a key-down event, if it maps to one."""
    if vk_code in _MODIFIER_VKS:
        return None
    key = _VK_TO_NAME.get(vk_code)
    if key is None:
        return None
    if not modifiers & {"ctrl", "alt", "win"}:
        return None
    ordered = [name for name in MODIFIER_ORDER if name in modifiers]
    return "+".join([*ordered, key])


class HotkeyListener:
    """Listen for bound hotkeys, suppress them, and dispatch off the hook thread."""

    def __init__(
        self,
        callback: Callable[[str], None],
        bindings: Mapping[str, str] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self._callback = callback
        self._on_error = on_error
        self._bindings: dict[str, str] = dict(bindings or {})
        self._lock = threading.Lock()
        self._queue: "queue.Queue[str | None]" = queue.Queue()
        self._listener = None
        self._dispatcher: threading.Thread | None = None
        self._running = False
        # Key-up events for a suppressed key-down are suppressed too, otherwise
        # the foreground application sees an unpaired key-up.
        self._suppressed_vks: set[int] = set()

    @property
    def bindings(self) -> dict[str, str]:
        with self._lock:
            return dict(self._bindings)

    def set_bindings(self, bindings: Mapping[str, str]) -> None:
        """Replace the hotkey table. Safe to call while running."""
        with self._lock:
            self._bindings = dict(bindings)
        log.info("hotkeys bound: %s", ", ".join(sorted(self._bindings)) or "none")

    def matches(self, hotkey: str | None) -> str | None:
        if hotkey is None:
            return None
        with self._lock:
            return self._bindings.get(hotkey)

    def start(self) -> None:
        if self._running:
            return
        if sys.platform != "win32":
            raise HotkeyError("the global hotkey listener requires Windows")

        from pynput import keyboard
        from pynput._util.win32 import SystemHook

        # suppress_event() signals pynput by raising; it must reach pynput.
        suppress_exception = SystemHook.SuppressException

        self._running = True
        self._dispatcher = threading.Thread(
            target=self._dispatch_loop, name="pedantic-hotkey-dispatch", daemon=True
        )
        self._dispatcher.start()

        listener_ref: dict[str, object] = {}

        def win32_event_filter(msg: int, data: object) -> bool | None:
            try:
                vk_code = int(getattr(data, "vkCode", -1))
                # Never react to the Ctrl+C/Ctrl+V this application injects.
                if is_injected_by_us(int(getattr(data, "dwExtraInfo", 0) or 0)):
                    return True
                if msg in _KEYUP_MESSAGES:
                    if vk_code in self._suppressed_vks:
                        self._suppressed_vks.discard(vk_code)
                        listener_ref["listener"].suppress_event()  # type: ignore[union-attr]
                    return True
                if msg not in _KEYDOWN_MESSAGES:
                    return True
                hotkey = hotkey_from_event(vk_code, _async_modifier_state())
                if hotkey is None:
                    return True
                if self.matches(hotkey) is None:
                    return True
                self._suppressed_vks.add(vk_code)
                self._queue.put(hotkey)
                listener_ref["listener"].suppress_event()  # type: ignore[union-attr]
            except suppress_exception:
                raise
            except Exception:
                # A raising hook callback can get the hook removed by Windows.
                log.debug("hotkey filter error", exc_info=True)
            return True

        self._listener = keyboard.Listener(win32_event_filter=win32_event_filter)
        listener_ref["listener"] = self._listener
        self._listener.start()
        log.info("global hotkey listener started")

    def stop(self) -> None:
        self._running = False
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                log.debug("hotkey listener did not stop cleanly", exc_info=True)
            self._listener = None
        self._queue.put(None)
        if self._dispatcher is not None:
            self._dispatcher.join(timeout=2)
            self._dispatcher = None

    def _dispatch_loop(self) -> None:
        while True:
            hotkey = self._queue.get()
            if hotkey is None:
                return
            try:
                self._callback(hotkey)
            except Exception as exc:
                log.exception("hotkey handler failed for %s", hotkey)
                if self._on_error is not None:
                    try:
                        self._on_error(exc)
                    except Exception:
                        log.exception("hotkey error callback failed")

    def __enter__(self) -> "HotkeyListener":
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()
