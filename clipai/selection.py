"""Win32 text-control helpers retained for diagnostics.

An earlier design read the selection directly from the focused control with
``EM_GETSEL`` and ``WM_GETTEXT``. That works only for classic Win32 edit
controls, so it failed in Chrome, Electron applications, Word, and anything
drawing its own text, which is why the shipping implementation copies through
the clipboard instead.

The helpers are kept because they answer the question that matters when a
hotkey appears to do nothing: *what window actually had focus, and does it look
like a text control?* Nothing here is on the transformation path.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass

log = logging.getLogger(__name__)

WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
EM_GETSEL = 0x00B0

# Window classes that behave like classic edit controls.
EDIT_CLASS_HINTS = ("edit", "richedit", "textbox", "scintilla", "notepad")


@dataclass(frozen=True)
class WindowInfo:
    """What Windows reports about the window that had focus."""

    handle: int = 0
    title: str = ""
    class_name: str = ""
    process_name: str = ""
    process_id: int = 0

    @property
    def looks_like_text_control(self) -> bool:
        lowered = self.class_name.lower()
        return any(hint in lowered for hint in EDIT_CLASS_HINTS)

    def describe(self) -> str:
        if not self.handle:
            return "no foreground window"
        parts = [f"hwnd={self.handle:#x}"]
        if self.process_name:
            parts.append(self.process_name)
        if self.class_name:
            parts.append(f"class={self.class_name}")
        if self.title:
            parts.append(f'title="{self.title[:60]}"')
        return " ".join(parts)


def _available() -> bool:
    return sys.platform == "win32"


def foreground_window() -> int:
    """Handle of the foreground window, or 0."""
    if not _available():
        return 0
    import ctypes

    return int(ctypes.windll.user32.GetForegroundWindow())


def focused_control(window: int | None = None) -> int:
    """Handle of the focused child control of the foreground thread, or 0.

    ``GetFocus`` is thread-local, so the foreground thread's input queue has to
    be attached first; that is what ``GetGUIThreadInfo`` does here.
    """
    if not _available():
        return 0
    import ctypes
    from ctypes import wintypes

    handle = window if window is not None else foreground_window()
    if not handle:
        return 0

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    class GUITHREADINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("hwndActive", wintypes.HWND),
            ("hwndFocus", wintypes.HWND),
            ("hwndCapture", wintypes.HWND),
            ("hwndMenuOwner", wintypes.HWND),
            ("hwndMoveSize", wintypes.HWND),
            ("hwndCaret", wintypes.HWND),
            ("rcCaret", RECT),
        ]

    user32 = ctypes.windll.user32
    thread_id = user32.GetWindowThreadProcessId(handle, None)
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(GUITHREADINFO)
    if not user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
        return 0
    return int(info.hwndFocus or 0)


def window_title(handle: int) -> str:
    if not _available() or not handle:
        return ""
    import ctypes

    user32 = ctypes.windll.user32
    length = int(user32.GetWindowTextLengthW(handle))
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(handle, buffer, length + 1)
    return buffer.value


def window_class(handle: int) -> str:
    if not _available() or not handle:
        return ""
    import ctypes

    buffer = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(handle, buffer, 256)
    return buffer.value


def window_process(handle: int) -> tuple[int, str]:
    """Process id and executable name owning a window."""
    if not _available() or not handle:
        return 0, ""
    import ctypes
    from ctypes import wintypes

    pid = wintypes.DWORD(0)
    ctypes.windll.user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
    if not pid.value:
        return 0, ""

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not process:
        return pid.value, ""
    try:
        size = wintypes.DWORD(260)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(
            process, 0, buffer, ctypes.byref(size)
        ):
            from pathlib import PureWindowsPath

            return pid.value, PureWindowsPath(buffer.value).name
    finally:
        kernel32.CloseHandle(process)
    return pid.value, ""


def describe_foreground() -> WindowInfo:
    """Collect diagnostics about the current foreground window."""
    handle = foreground_window()
    if not handle:
        return WindowInfo()
    control = focused_control(handle) or handle
    pid, process_name = window_process(handle)
    return WindowInfo(
        handle=control,
        title=window_title(handle),
        class_name=window_class(control),
        process_name=process_name,
        process_id=pid,
    )


def selection_range(control: int) -> tuple[int, int] | None:
    """Selection start and end in a classic edit control, if it reports one."""
    if not _available() or not control:
        return None
    import ctypes
    from ctypes import wintypes

    start = wintypes.DWORD(0)
    end = wintypes.DWORD(0)
    try:
        ctypes.windll.user32.SendMessageW(
            control, EM_GETSEL, ctypes.byref(start), ctypes.byref(end)
        )
    except Exception:
        return None
    if start.value == end.value:
        return None
    return int(start.value), int(end.value)


def control_text(control: int, limit: int = 65536) -> str:
    """Full text of a classic edit control via ``WM_GETTEXT``."""
    if not _available() or not control:
        return ""
    import ctypes

    user32 = ctypes.windll.user32
    length = int(user32.SendMessageW(control, WM_GETTEXTLENGTH, 0, 0))
    if length <= 0:
        return ""
    size = min(length, limit) + 1
    buffer = ctypes.create_unicode_buffer(size)
    user32.SendMessageW(control, WM_GETTEXT, size, buffer)
    return buffer.value


def selected_text_via_win32() -> str | None:
    """Best-effort direct read of the selection. Diagnostics only.

    Returns None whenever the focused control is not a classic edit control or
    reports no selection, which is the common case in modern applications.
    """
    control = focused_control()
    if not control:
        return None
    bounds = selection_range(control)
    if bounds is None:
        return None
    text = control_text(control)
    if not text:
        return None
    start, end = bounds
    if start >= len(text):
        return None
    return text[start:end]
