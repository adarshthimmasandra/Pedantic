"""Clipboard sequence polling and snapshot restore.

The real clipboard functions need Windows and a live clipboard owner, so these
tests drive the sequence-number logic with a stubbed sequence counter. The
marked integration tests exercise the real thing.
"""

from __future__ import annotations

from clipai import clipboard
from clipai.clipboard import ClipboardSnapshot


def test_wait_for_change_polls_the_sequence_number_until_it_moves(monkeypatch):
    sequence = iter([100, 100, 101])
    monkeypatch.setattr(clipboard, "get_sequence_number", lambda: next(sequence))
    assert clipboard.wait_for_change(100, timeout_ms=500, poll_interval=0.0) == 101

    # A copy that never lands is what "could not copy selected text" means.
    monkeypatch.setattr(clipboard, "get_sequence_number", lambda: 100)
    assert clipboard.wait_for_change(100, timeout_ms=0, poll_interval=0.0) is None


def test_copy_and_read_returns_the_new_text_or_nothing(monkeypatch):
    sent: list[str] = []
    state = {"sequence": 100, "text": "old"}

    def send_copy() -> None:
        sent.append("ctrl+c")
        state["sequence"] += 1
        state["text"] = "new selection"

    monkeypatch.setattr(clipboard, "get_sequence_number", lambda: state["sequence"])
    monkeypatch.setattr(clipboard, "read_text", lambda: state["text"])

    assert clipboard.copy_and_read(100, 500, send_copy=send_copy) == "new selection"
    assert sent == ["ctrl+c"]

    # A copy that does not move the sequence number yields nothing, even though
    # the clipboard still holds readable text from before.
    assert clipboard.copy_and_read(state["sequence"], 0, send_copy=lambda: None) is None


def test_only_plain_text_snapshots_are_restored(monkeypatch):
    written: list[str] = []
    monkeypatch.setattr(clipboard, "write_text", lambda text: written.append(text))

    assert clipboard.restore(ClipboardSnapshot("before", True, 100)) is True
    assert written == ["before"]

    # An image or file list cannot be reconstructed, so it is left untouched
    # rather than emptied.
    non_text = ClipboardSnapshot(None, False, 100)
    assert non_text.restorable is False
    assert clipboard.restore(non_text) is False
    assert written == ["before"]
