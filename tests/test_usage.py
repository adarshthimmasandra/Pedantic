"""Token/cost records and budget enforcement."""

from __future__ import annotations

import datetime as dt

import pytest

from clipai import paths, usage
from clipai.usage import BudgetExceeded, UsageRecord


def test_pricing_uses_the_longest_matching_prefix_and_costs_per_million():
    # Model ids carry a date suffix, so lookup is by prefix.
    assert usage.pricing_for("claude-haiku-4-5-20251001") == (1.0, 5.0)
    assert usage.pricing_for("claude-3-haiku-20240307") == (0.25, 1.25)
    assert usage.pricing_for("some-unknown-model") == usage.DEFAULT_PRICING

    assert usage.estimate_cost(
        "claude-haiku-4-5-20251001", 1_000_000, 1_000_000
    ) == pytest.approx(6.0)
    assert usage.estimate_cost("claude-haiku-4-5-20251001", 1000, 500) == pytest.approx(
        0.0035
    )


def test_records_round_trip_and_corrupt_lines_are_skipped(isolated_home):
    record = UsageRecord.create("grammar", "claude-haiku-4-5-20251001", 100, 50)
    assert UsageRecord.from_json(record.to_json()) == record

    path = paths.usage_path()
    path.write_text(f"{record.to_json()}\nnot json\n{{}}\n", encoding="utf-8")
    records = usage.read_records(path)
    assert len(records) == 1
    assert records[0].profile == "grammar"


def test_monthly_summaries_accumulate_appended_records(isolated_home):
    usage.record_usage("grammar", "claude-haiku-4-5-20251001", 1000, 500)
    usage.record_usage("formal", "claude-haiku-4-5-20251001", 2000, 1000)

    summary = usage.month_summary()
    assert summary.requests == 2
    assert summary.input_tokens == 3000
    assert summary.output_tokens == 1500
    assert summary.total_tokens == 4500
    assert summary.cost_usd == pytest.approx(0.0105)


def test_the_usage_report_totals_every_month(isolated_home):
    assert usage.format_usage_report() == "No usage recorded yet."

    usage.record_usage("grammar", "claude-haiku-4-5-20251001", 1000, 500)
    report = usage.format_usage_report()
    assert f"{dt.date.today():%Y-%m}" in report
    assert "Total" in report


def test_the_budget_blocks_only_once_a_positive_limit_is_reached(isolated_home):
    # A limit of zero means unlimited.
    usage.record_usage("grammar", "claude-haiku-4-5-20251001", 10_000_000, 0)
    usage.check_budget(0.0)

    # 10M input tokens at $1/M is $10, which is past a $5 limit.
    with pytest.raises(BudgetExceeded, match="Monthly budget reached"):
        usage.check_budget(5.0)
