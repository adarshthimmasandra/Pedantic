"""Token/cost records and budget enforcement.

Every request appends one JSON line to a per-month journal. Append-only JSONL
is used rather than a rewritten document because records are written from the
transformation path, where a partially written file must not lose earlier
history.

Costs are computed locally from published per-token prices. They are an
estimate for the user's own awareness, not a billing record.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from . import paths

log = logging.getLogger(__name__)

TOKENS_PER_MILLION = 1_000_000

# USD per million tokens, (input, output).
PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-3-5-haiku": (0.80, 4.0),
    "claude-3-haiku": (0.25, 1.25),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-3-7-sonnet": (3.0, 15.0),
    "claude-opus-4": (15.0, 75.0),
}
DEFAULT_PRICING = (1.0, 5.0)


class BudgetExceeded(Exception):
    """Raised when the configured monthly spend limit has been reached."""


def pricing_for(model: str) -> tuple[float, float]:
    """Find prices for a model id, matching the longest known prefix.

    Model ids carry a date suffix (``claude-haiku-4-5-20251001``), so an exact
    lookup would go stale with every model refresh.
    """
    name = (model or "").strip().lower()
    best: tuple[float, float] | None = None
    best_length = -1
    for prefix, prices in PRICING.items():
        if name.startswith(prefix) and len(prefix) > best_length:
            best, best_length = prices, len(prefix)
    return best or DEFAULT_PRICING


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Cost in USD for one request."""
    input_price, output_price = pricing_for(model)
    return (
        input_tokens * input_price + output_tokens * output_price
    ) / TOKENS_PER_MILLION


@dataclass(frozen=True)
class UsageRecord:
    """One billable request."""

    timestamp: str
    profile: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float

    @classmethod
    def create(
        cls, profile: str, model: str, input_tokens: int, output_tokens: int
    ) -> "UsageRecord":
        return cls(
            timestamp=_dt.datetime.now().isoformat(timespec="seconds"),
            profile=profile,
            model=model,
            input_tokens=int(input_tokens),
            output_tokens=int(output_tokens),
            cost_usd=round(estimate_cost(model, input_tokens, output_tokens), 6),
        )

    def to_json(self) -> str:
        return json.dumps(
            {
                "timestamp": self.timestamp,
                "profile": self.profile,
                "model": self.model,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cost_usd": self.cost_usd,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, line: str) -> "UsageRecord | None":
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        # A JSON object without these is not a usage record, so it is dropped
        # rather than counted as a free request.
        if not data.get("timestamp") or not data.get("model"):
            return None
        try:
            return cls(
                timestamp=str(data.get("timestamp", "")),
                profile=str(data.get("profile", "")),
                model=str(data.get("model", "")),
                input_tokens=int(data.get("input_tokens", 0) or 0),
                output_tokens=int(data.get("output_tokens", 0) or 0),
                cost_usd=float(data.get("cost_usd", 0.0) or 0.0),
            )
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True)
class UsageSummary:
    """Aggregated usage over a set of records."""

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def describe(self, label: str = "This month") -> str:
        return (
            f"{label}: {self.requests} requests, "
            f"{self.input_tokens:,} in / {self.output_tokens:,} out tokens, "
            f"${self.cost_usd:.4f}"
        )


def summarize(records: Iterable[UsageRecord]) -> UsageSummary:
    requests = 0
    input_tokens = 0
    output_tokens = 0
    cost = 0.0
    for record in records:
        requests += 1
        input_tokens += record.input_tokens
        output_tokens += record.output_tokens
        cost += record.cost_usd
    return UsageSummary(requests, input_tokens, output_tokens, round(cost, 6))


def read_records(path: Path) -> list[UsageRecord]:
    """Read a usage journal, skipping any line that is not a valid record."""
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        log.warning("could not read usage journal %s: %s", path, exc)
        return []
    records = [UsageRecord.from_json(line) for line in lines if line.strip()]
    return [record for record in records if record is not None]


def append_record(record: UsageRecord, path: Path | None = None) -> Path:
    """Append one record to the current month's journal."""
    target = Path(path) if path else paths.usage_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(record.to_json() + "\n")
    except OSError as exc:
        log.warning("could not record usage: %s", exc)
    return target


def record_usage(
    profile: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    path: Path | None = None,
) -> UsageRecord:
    record = UsageRecord.create(profile, model, input_tokens, output_tokens)
    append_record(record, path)
    return record


def month_summary(when: _dt.date | None = None, path: Path | None = None) -> UsageSummary:
    target = Path(path) if path else paths.usage_path(when)
    return summarize(read_records(target))


def all_summaries() -> list[tuple[str, UsageSummary]]:
    """Per-month summaries, oldest first, for ``--usage-summary``."""
    summaries: list[tuple[str, UsageSummary]] = []
    for file in paths.usage_files():
        label = file.stem.replace(paths.USAGE_FILE_PREFIX, "")
        summaries.append((label, summarize(read_records(file))))
    return summaries


def format_usage_report(summaries: Sequence[tuple[str, UsageSummary]] | None = None) -> str:
    """Human-readable usage report."""
    rows = list(summaries) if summaries is not None else all_summaries()
    if not rows:
        return "No usage recorded yet."
    lines = ["Month     Requests   Input      Output     Cost (USD)"]
    total = UsageSummary()
    for label, summary in rows:
        lines.append(
            f"{label:<9} {summary.requests:>8}   "
            f"{summary.input_tokens:>9,} {summary.output_tokens:>10,}   "
            f"{summary.cost_usd:>9.4f}"
        )
        total = UsageSummary(
            total.requests + summary.requests,
            total.input_tokens + summary.input_tokens,
            total.output_tokens + summary.output_tokens,
            round(total.cost_usd + summary.cost_usd, 6),
        )
    lines.append(
        f"{'Total':<9} {total.requests:>8}   "
        f"{total.input_tokens:>9,} {total.output_tokens:>10,}   "
        f"{total.cost_usd:>9.4f}"
    )
    return "\n".join(lines)


def check_budget(
    monthly_limit: float, when: _dt.date | None = None, path: Path | None = None
) -> None:
    """Raise :class:`BudgetExceeded` when this month's spend reached the limit.

    A limit of zero or less means unlimited. The check happens before a request
    is sent, so the limit is a stop, not a warning after the fact.
    """
    if monthly_limit is None or monthly_limit <= 0:
        return
    spent = month_summary(when, path).cost_usd
    if spent >= monthly_limit:
        raise BudgetExceeded(
            f"Monthly budget reached: ${spent:.2f} of ${monthly_limit:.2f}. "
            "Raise monthly_usd_limit in config.toml to continue."
        )
