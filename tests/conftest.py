"""Shared fixtures.

Every test runs against an isolated ``CLIPAI_HOME`` so nothing touches the
developer's real ``%APPDATA%\\clipai`` directory, and the Windows-only
collaborators (clipboard, keyboard, network) are replaced by fakes that record
what the pipeline asked them to do.
"""

from __future__ import annotations

import pytest

from clipai import paths
from clipai.backends.base import Backend, TransformRequest, TransformResult
from clipai.clipboard import ClipboardSnapshot
from clipai.config import DEFAULT_CONFIG_TOML, default_config, loads


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Point all per-user data at a temporary directory."""
    home = tmp_path / "clipai-home"
    home.mkdir()
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(home))
    return home


@pytest.fixture
def config():
    return default_config()


@pytest.fixture
def default_toml():
    return DEFAULT_CONFIG_TOML


@pytest.fixture
def config_from_toml():
    return loads


class FakeClipboard:
    """A clipboard whose sequence number only moves when something writes.

    This mirrors the real Windows behavior the pipeline depends on: a copy is
    detected by the sequence number changing, not by the text differing.
    """

    def __init__(self, text: str | None = "original", copy_result: str | None = "  hello  "):
        self.text = text
        self.sequence = 100
        self.copy_result = copy_result
        self.writes: list[str] = []
        self.restored: list[str | None] = []
        self.copy_calls = 0
        self.fail_on_write = False

    def get_sequence_number(self) -> int:
        return self.sequence

    def read_text(self) -> str | None:
        return self.text

    def write_text(self, text: str) -> None:
        if self.fail_on_write:
            raise RuntimeError("clipboard is locked")
        self.text = text
        self.sequence += 1
        self.writes.append(text)

    def snapshot(self) -> ClipboardSnapshot:
        return ClipboardSnapshot(
            text=self.text, had_text=self.text is not None, sequence=self.sequence
        )

    def restore(self, previous: ClipboardSnapshot) -> bool:
        if not previous.restorable:
            self.restored.append(None)
            return False
        self.text = previous.text
        self.sequence += 1
        self.restored.append(previous.text)
        return True

    def copy_and_read(self, baseline_sequence, timeout_ms, send_copy=None):
        self.copy_calls += 1
        if send_copy is not None:
            send_copy()
        if self.copy_result is None:
            return None
        self.text = self.copy_result
        self.sequence += 1
        return self.copy_result


class FakeKeys:
    """Records the synthetic keystrokes the pipeline requested."""

    def __init__(self, modifiers_released: bool = True):
        self.events: list[str] = []
        self.modifiers_released = modifiers_released
        self.paste_error: Exception | None = None

    def wait_for_modifier_release(self, timeout: float = 1.0) -> bool:
        self.events.append("wait")
        return self.modifiers_released

    def send_ctrl_c(self) -> None:
        self.events.append("ctrl+c")

    def send_ctrl_v(self) -> None:
        if self.paste_error is not None:
            raise self.paste_error
        self.events.append("ctrl+v")


class FakeBackend(Backend):
    """A backend that echoes a canned response and records its requests."""

    name = "fake"

    def __init__(self, response: str = "Hello.", error: Exception | None = None):
        self.response = response
        self.error = error
        self.requests: list[TransformRequest] = []
        self.closed = False

    def transform(self, request: TransformRequest) -> TransformResult:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return TransformResult(
            text=self.response,
            model=request.model,
            input_tokens=11,
            output_tokens=7,
        )

    def close(self) -> None:
        self.closed = True


class RecordingNotifier:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    def __call__(self, title: str, message: str, level: str) -> None:
        self.messages.append((title, message, level))

    @property
    def levels(self) -> list[str]:
        return [level for _, _, level in self.messages]


@pytest.fixture
def fake_clipboard():
    return FakeClipboard()


@pytest.fixture
def fake_keys():
    return FakeKeys()


@pytest.fixture
def fake_backend():
    return FakeBackend()


@pytest.fixture
def make_backend():
    """Build additional fake backends, e.g. one that always fails."""
    return FakeBackend


@pytest.fixture
def notifier():
    return RecordingNotifier()


@pytest.fixture
def make_app(fake_clipboard, fake_keys, fake_backend, notifier):
    """Build a :class:`PedanticApp` wired entirely to fakes."""
    from clipai.app import PedanticApp

    def factory(**overrides):
        settings: dict = {
            "config": default_config(),
            "clipboard": fake_clipboard,
            "keys": fake_keys,
            "backend": fake_backend,
            "notifier": notifier,
            "sleep": lambda _seconds: None,
        }
        settings.update(overrides)
        app = PedanticApp(**settings)
        # Notifications are opt-in per level; tests want to see all of them.
        return app

    return factory
