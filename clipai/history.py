"""Local history.

The last few transformations are kept so a user can recover a result that was
pasted into the wrong window, or compare what they selected with what came
back. The file is capped and rewritten atomically, and it is deliberately
excluded from source and portable archives because it contains work text.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import paths
from .cleaning import preview

log = logging.getLogger(__name__)

MAX_ENTRIES = 50


@dataclass(frozen=True)
class HistoryEntry:
    """One completed transformation."""

    timestamp: str
    profile: str
    original: str
    result: str
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0

    @classmethod
    def create(
        cls,
        profile: str,
        original: str,
        result: str,
        model: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> "HistoryEntry":
        return cls(
            timestamp=_dt.datetime.now().isoformat(timespec="seconds"),
            profile=profile,
            original=original,
            result=result,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    @property
    def label(self) -> str:
        """Short one-line description for the tray menu."""
        when = self.timestamp[11:16] if len(self.timestamp) >= 16 else self.timestamp
        return f"{when} {self.profile}: {preview(self.result, 48)}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "HistoryEntry | None":
        if not isinstance(data, dict):
            return None
        try:
            return cls(
                timestamp=str(data.get("timestamp", "")),
                profile=str(data.get("profile", "")),
                original=str(data.get("original", "")),
                result=str(data.get("result", "")),
                model=str(data.get("model", "") or ""),
                input_tokens=int(data.get("input_tokens", 0) or 0),
                output_tokens=int(data.get("output_tokens", 0) or 0),
            )
        except (TypeError, ValueError):
            return None


@dataclass
class History:
    """A bounded, newest-first list of transformations backed by a JSON file."""

    path: Path | None = None
    max_entries: int = MAX_ENTRIES
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        self.path = Path(self.path) if self.path else paths.history_path()

    def load(self) -> list[HistoryEntry]:
        """Read the history file, tolerating corruption by starting over."""
        assert self.path is not None
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("ignoring unreadable history file: %s", exc)
            return []
        if not isinstance(raw, list):
            return []
        entries = [HistoryEntry.from_dict(item) for item in raw]
        return [entry for entry in entries if entry is not None]

    def save(self, entries: list[HistoryEntry]) -> None:
        """Write the history file atomically so a crash cannot truncate it."""
        assert self.path is not None
        trimmed = entries[: self.max_entries]
        payload = json.dumps(
            [entry.to_dict() for entry in trimmed], indent=2, ensure_ascii=False
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(payload, encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError as exc:
            log.warning("could not write history: %s", exc)

    def add(self, entry: HistoryEntry) -> list[HistoryEntry]:
        """Prepend an entry and persist. Returns the new list."""
        with self._lock:
            entries = [entry, *self.load()][: self.max_entries]
            self.save(entries)
            return entries

    def record(
        self,
        profile: str,
        original: str,
        result: str,
        model: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> HistoryEntry:
        entry = HistoryEntry.create(
            profile, original, result, model, input_tokens, output_tokens
        )
        self.add(entry)
        return entry

    def latest(self, count: int = 10) -> list[HistoryEntry]:
        return self.load()[:count]

    def clear(self) -> None:
        with self._lock:
            assert self.path is not None
            self.save([])
            log.info("cleared history")
