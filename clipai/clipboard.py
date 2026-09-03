"""Clipboard read, write, snapshot, poll, and restore.

The clipboard is shared mutable state owned by whichever process opened it
last, so every operation here is defensive:

*Detecting a copy.* Comparing clipboard text before and after a synthetic
``Ctrl+C`` is unreliable, because copying the same text twice produces no
visible difference. Windows maintains a clipboard sequence number that changes
on every update, and :func:`wait_for_change` polls that instead.

*Opening the clipboard.* ``OpenClipboard`` fails while another process holds it,
which happens constantly with clipboard managers and Office. Every access
retries briefly before giving up.

*Restoring.* Only plain Unicode text is captured and restored. Images, files,
and rich formats cannot be reconstructed, so the snapshot records that the
previous content was non-text and skips the restore rather than destroying it.
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

CF_UNICODETEXT = 13

OPEN_RETRIES = 12
OPEN_RETRY_DELAY = 0.02


class ClipboardError(Exception):
    """Raised when the clipboard cannot be read or written."""


@dataclass(frozen=True)
class ClipboardSnapshot:
    """The clipboard content captured before a transformation."""

    text: str | None
    had_text: bool
    sequence: int

    @property
    def restorable(self) -> bool:
        return self.had_text and self.text is not None


def _win32clipboard():
    if sys.platform != "win32":
        raise ClipboardError("clipboard access requires Windows")
    try:
        import win32clipboard
    except ImportError as exc:  # pragma: no cover - pywin32 is a dependency
        raise ClipboardError("pywin32 is required for clipboard access") from exc
    return win32clipboard


def get_sequence_number() -> int:
    """Return the Windows clipboard sequence number.

    The value changes every time any process updates the clipboard, and it does
    not require opening the clipboard, so it is safe to poll.
    """
    if sys.platform != "win32":
        raise ClipboardError("clipboard access requires Windows")
    import ctypes

    return int(ctypes.windll.user32.GetClipboardSequenceNumber())


class _OpenClipboard:
    """Context manager that opens the clipboard with bounded retries."""

    def __init__(self, retries: int = OPEN_RETRIES, delay: float = OPEN_RETRY_DELAY):
        self._retries = retries
        self._delay = delay
        self._module = _win32clipboard()

    def __enter__(self):
        last_error: Exception | None = None
        for attempt in range(self._retries):
            try:
                self._module.OpenClipboard()
                return self._module
            except Exception as exc:
                last_error = exc
                time.sleep(self._delay * (attempt + 1))
        raise ClipboardError(
            "another application is holding the clipboard open"
        ) from last_error

    def __exit__(self, *exc_info: object) -> None:
        try:
            self._module.CloseClipboard()
        except Exception:
            log.debug("CloseClipboard failed", exc_info=True)


def has_text() -> bool:
    """True when the clipboard currently holds Unicode text."""
    module = _win32clipboard()
    try:
        with _OpenClipboard():
            return bool(module.IsClipboardFormatAvailable(CF_UNICODETEXT))
    except ClipboardError:
        return False


def read_text() -> str | None:
    """Read plain Unicode text, or None when the clipboard holds something else."""
    module = _win32clipboard()
    with _OpenClipboard():
        if not module.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        try:
            value = module.GetClipboardData(CF_UNICODETEXT)
        except Exception as exc:
            raise ClipboardError(f"could not read clipboard text: {exc}") from exc
    if value is None:
        return None
    return str(value)


def write_text(text: str) -> None:
    """Replace the clipboard content with plain Unicode text."""
    with _OpenClipboard() as clipboard:
        try:
            clipboard.EmptyClipboard()
            clipboard.SetClipboardData(CF_UNICODETEXT, str(text))
        except Exception as exc:
            raise ClipboardError(f"could not write clipboard text: {exc}") from exc
    log.debug("wrote %d characters to the clipboard", len(text))


def clear() -> None:
    """Empty the clipboard."""
    with _OpenClipboard() as clipboard:
        clipboard.EmptyClipboard()


def snapshot() -> ClipboardSnapshot:
    """Capture the current clipboard so it can be restored after pasting."""
    sequence = get_sequence_number()
    try:
        text = read_text()
    except ClipboardError as exc:
        log.warning("could not snapshot the clipboard: %s", exc)
        return ClipboardSnapshot(text=None, had_text=False, sequence=sequence)
    return ClipboardSnapshot(text=text, had_text=text is not None, sequence=sequence)


def restore(previous: ClipboardSnapshot) -> bool:
    """Restore a snapshot. Returns True when the clipboard was rewritten.

    A snapshot that held no text is left alone; emptying the clipboard would
    lose an image or file list that is still there.
    """
    if not previous.restorable:
        log.debug("skipping clipboard restore: previous content was not plain text")
        return False
    try:
        write_text(previous.text or "")
    except ClipboardError as exc:
        log.warning("could not restore the clipboard: %s", exc)
        return False
    log.debug("restored previous clipboard content")
    return True


def wait_for_change(
    baseline_sequence: int, timeout_ms: int, poll_interval: float = 0.015
) -> int | None:
    """Wait for the clipboard sequence number to move past ``baseline_sequence``.

    Returns the new sequence number, or None if nothing changed within the
    timeout, which is what "could not copy selected text" means.
    """
    deadline = time.monotonic() + (max(timeout_ms, 0) / 1000.0)
    while True:
        current = get_sequence_number()
        if current != baseline_sequence:
            return current
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll_interval)


def copy_and_read(
    baseline_sequence: int, timeout_ms: int, send_copy=None
) -> str | None:
    """Simulate a copy and return the newly copied text.

    Returns None when the clipboard never changed or the new content is not
    text. ``send_copy`` is injectable so the sequence can be tested without a
    real keyboard.
    """
    if send_copy is None:
        from .keys import send_ctrl_c as send_copy  # local import: Windows only

    send_copy()
    changed = wait_for_change(baseline_sequence, timeout_ms)
    if changed is None:
        return None
    return read_text()
