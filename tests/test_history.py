"""Local history."""

from __future__ import annotations

from clipai.history import History, HistoryEntry


def test_entries_are_newest_first_and_capped(isolated_home):
    history = History(max_entries=3)
    for index in range(6):
        history.record("grammar", f"in {index}", f"out {index}")

    entries = history.load()
    assert len(entries) == 3
    assert entries[0].result == "out 5"
    assert entries[-1].result == "out 3"


def test_history_survives_corruption_and_can_be_cleared(isolated_home):
    history = History()
    assert history.path is not None
    history.path.write_text("{not json", encoding="utf-8")
    assert history.load() == []

    history.record("grammar", "teh cat", "the cat")
    history.record("formal", "hi", "Hello.")
    entries = history.load()
    assert [entry.profile for entry in entries] == ["formal", "grammar"]

    history.clear()
    assert history.load() == []


def test_entry_labels_are_short_and_single_line():
    entry = HistoryEntry.create("grammar", "in", "a result\nwith  newlines")
    assert "\n" not in entry.label
    assert "grammar" in entry.label
