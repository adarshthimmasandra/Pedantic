"""Single-instance lock and OS actions."""

from __future__ import annotations

import os

import pytest

from clipai import paths
from clipai.platform import AlreadyRunning, SingleInstanceLock, startup_command


def test_the_lock_is_exclusive_and_records_the_pid(isolated_home):
    first = SingleInstanceLock()
    assert first.acquire() is True
    assert first.other_pid() == os.getpid()

    # A second instance sharing this configuration directory must be refused,
    # otherwise both would bind the same global hotkeys.
    second = SingleInstanceLock()
    assert second.acquire() is False
    with pytest.raises(AlreadyRunning, match="already running"):
        with SingleInstanceLock():
            pass

    first.release()
    assert second.acquire() is True
    second.release()
    assert paths.lock_path().exists()


def test_startup_command_quotes_the_executable():
    command = startup_command()
    assert command.startswith('"')
    assert command.count('"') >= 2
