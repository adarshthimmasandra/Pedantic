"""Per-user data locations."""

from __future__ import annotations

import datetime as dt

from clipai import paths


def test_all_runtime_files_live_in_one_directory(isolated_home):
    assert paths.data_dir() == isolated_home
    for path in (
        paths.config_path(),
        paths.log_path(),
        paths.history_path(),
        paths.lock_path(),
        paths.usage_path(),
    ):
        assert path.parent == isolated_home

    assert paths.config_path().name == "config.toml"
    assert paths.log_path().name == "clipai.log"
    assert paths.history_path().name == "history.json"
    assert paths.lock_path().name == "clipai.lock"


def test_usage_files_are_named_per_month_and_discovered_in_order(isolated_home):
    assert paths.usage_path(dt.date(2026, 9, 3)).name == "usage-2026-09.jsonl"
    assert paths.usage_path(dt.date(2026, 12, 31)).name == "usage-2026-12.jsonl"

    for month in ("2026-07", "2026-09", "2026-08"):
        (isolated_home / f"usage-{month}.jsonl").write_text("", encoding="utf-8")
    (isolated_home / "history.json").write_text("[]", encoding="utf-8")

    assert [path.name for path in paths.usage_files()] == [
        "usage-2026-07.jsonl",
        "usage-2026-08.jsonl",
        "usage-2026-09.jsonl",
    ]


def test_the_default_windows_location_is_under_appdata(monkeypatch, tmp_path):
    monkeypatch.delenv(paths.HOME_ENV_VAR, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setattr(paths.sys, "platform", "win32")

    assert paths.data_dir() == tmp_path / "Roaming" / "clipai"
