"""Synthetic Ctrl+C and Ctrl+V via the Windows ``SendInput`` API.

``SendInput`` is used rather than a higher-level automation library because it
injects at the same level as a real keyboard, which is what applications like
Word, Outlook, Teams, and Chrome expect for copy and paste.

Two details matter for reliability:

*Clean* keystrokes. The user is still physically holding the hotkey modifiers
when the hotkey fires. Sending ``Ctrl+C`` while ``Shift`` is down produces
``Ctrl+Shift+C``, which many applications treat as something else entirely.
:func:`wait_for_modifier_release` therefore waits for the physical keys to come
up, and any modifier still stuck down is explicitly released first.

*Identifiable* keystrokes. Every injected event carries
:data:`INJECTED_SIGNATURE` in ``dwExtraInfo`` so the global hotkey hook can
recognize its own synthetic input and ignore it instead of recursing.
"""

from __future__ import annotations

import logging
import sys
import time

log = logging.getLogger(__name__)

# "PDNT" - marks keyboard events injected by this application.
INJECTED_SIGNATURE = 0x5044_4E54

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

LLKHF_INJECTED = 0x10

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_C = 0x43
VK_V = 0x56

_MODIFIER_VKS = (VK_CONTROL, VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN)

# Extended keys must carry KEYEVENTF_EXTENDEDKEY for their scan code to be
# interpreted correctly.
_EXTENDED_VKS = frozenset({VK_RWIN, 0xA3, 0xA5})

DEFAULT_KEY_DELAY = 0.012
MODIFIER_RELEASE_TIMEOUT = 1.0


class InputError(Exception):
    """Raised when Windows refuses to accept injected keyboard input."""


def _win32():
    """Build the ctypes structures and prototypes for ``SendInput``."""
    import ctypes
    from ctypes import wintypes

    ulong_ptr = wintypes.WPARAM  # matches pointer width on 32- and 64-bit

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ulong_ptr),
        ]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ulong_ptr),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("union",)
        _fields_ = [("type", wintypes.DWORD), ("union", _INPUTUNION)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.SendInput.restype = wintypes.UINT
    user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
    user32.MapVirtualKeyW.restype = wintypes.UINT
    user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
    user32.GetAsyncKeyState.restype = ctypes.c_short
    return ctypes, INPUT, user32


def _require_windows() -> None:
    if sys.platform != "win32":
        raise InputError("synthetic keyboard input requires Windows")


def is_modifier_physically_down() -> bool:
    """True while any Ctrl, Shift, Alt, or Win key is physically held."""
    _require_windows()
    _, _, user32 = _win32()
    return any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in _MODIFIER_VKS)


def wait_for_modifier_release(
    timeout: float = MODIFIER_RELEASE_TIMEOUT, poll_interval: float = 0.01
) -> bool:
    """Wait for the user to let go of every modifier key.

    Returns True if the keyboard became clean, False on timeout. A timeout is
    not fatal: :func:`send_ctrl_c` releases whatever is still held.
    """
    _require_windows()
    deadline = time.monotonic() + max(timeout, 0.0)
    while True:
        if not is_modifier_physically_down():
            return True
        if time.monotonic() >= deadline:
            log.debug("modifier keys still held after %.2fs", timeout)
            return False
        time.sleep(poll_interval)


def _build_events(ctypes_mod, INPUT, user32, keys: list[tuple[int, bool]]):
    """Create an INPUT array for a sequence of (virtual key, is_key_up) pairs."""
    array = (INPUT * len(keys))()
    for index, (vk, key_up) in enumerate(keys):
        flags = 0
        if key_up:
            flags |= KEYEVENTF_KEYUP
        if vk in _EXTENDED_VKS:
            flags |= KEYEVENTF_EXTENDEDKEY
        event = array[index]
        event.type = INPUT_KEYBOARD
        event.ki.wVk = vk
        # Applications that read scan codes rather than virtual keys need this.
        event.ki.wScan = user32.MapVirtualKeyW(vk, 0)
        event.ki.dwFlags = flags
        event.ki.time = 0
        event.ki.dwExtraInfo = INJECTED_SIGNATURE
    return array


def send_key_sequence(
    keys: list[tuple[int, bool]], delay: float = DEFAULT_KEY_DELAY
) -> None:
    """Inject a sequence of key events, one call per event.

    Events are sent individually with a short pause. Batching the whole
    combination into a single ``SendInput`` call is faster but some applications
    miss the key-down/key-up pairing when it arrives within the same tick.
    """
    _require_windows()
    if not keys:
        return
    ctypes_mod, INPUT, user32 = _win32()
    for pair in keys:
        array = _build_events(ctypes_mod, INPUT, user32, [pair])
        sent = user32.SendInput(1, array, ctypes_mod.sizeof(INPUT))
        if sent != 1:
            error = ctypes_mod.get_last_error()
            raise InputError(
                f"SendInput was blocked for virtual key {pair[0]:#x} (error {error})"
            )
        if delay:
            time.sleep(delay)


def release_stuck_modifiers() -> list[int]:
    """Release any modifier Windows still reports as held.

    Returns the virtual keys that had to be released, which is useful for logs
    when an application swallowed a key-up.
    """
    _require_windows()
    _, _, user32 = _win32()
    stuck = [vk for vk in _MODIFIER_VKS if user32.GetAsyncKeyState(vk) & 0x8000]
    if stuck:
        log.debug("releasing stuck modifiers: %s", [hex(vk) for vk in stuck])
        send_key_sequence([(vk, True) for vk in stuck])
    return stuck


def _send_ctrl_combination(vk: int, delay: float) -> None:
    release_stuck_modifiers()
    send_key_sequence(
        [
            (VK_CONTROL, False),
            (vk, False),
            (vk, True),
            (VK_CONTROL, True),
        ],
        delay=delay,
    )


def send_ctrl_c(delay: float = DEFAULT_KEY_DELAY) -> None:
    """Send a clean ``Ctrl+C`` with no other modifier held."""
    _send_ctrl_combination(VK_C, delay)
    log.debug("sent synthetic ctrl+c")


def send_ctrl_v(delay: float = DEFAULT_KEY_DELAY) -> None:
    """Send a clean ``Ctrl+V`` with no other modifier held."""
    _send_ctrl_combination(VK_V, delay)
    log.debug("sent synthetic ctrl+v")


def is_injected_by_us(dw_extra_info: int) -> bool:
    """True when a hook event carries this application's injection marker."""
    return dw_extra_info == INJECTED_SIGNATURE
