"""Default TOML, validation, and live reload."""

from __future__ import annotations

import pytest

from clipai import config as config_module
from clipai.config import (
    Config,
    ConfigError,
    ConfigWatcher,
    default_config,
    dump_config,
    load_config,
    loads,
    write_default_config,
)

MINIMAL_PROFILE = """
[[profile]]
name = "grammar"
hotkey = "ctrl+shift+g"
prompt = "Fix it."
"""


def test_default_config_matches_the_documented_settings():
    config = default_config()
    assert config.api.model == "claude-haiku-4-5-20251001"
    assert config.api.timeout_seconds == 30
    assert config.api.max_attempts == 3
    assert config.api.max_tokens_ceiling == 2048
    assert config.api.max_input_chars == 8000
    assert config.behavior.paste_delay_ms == 300
    assert config.behavior.clipboard_poll_timeout_ms == 700
    assert config.behavior.restore_clipboard is True
    assert config.behavior.notify_on_success is False
    assert config.behavior.notify_on_error is True
    # No spend limit by default.
    assert config.budget.enforced is False


def test_default_config_ships_the_five_documented_profiles():
    profiles = default_config().profiles
    assert [profile.name for profile in profiles] == [
        "grammar",
        "formal",
        "concise",
        "bullets",
        "reply",
    ]
    assert [profile.hotkey for profile in profiles] == [
        "ctrl+shift+g",
        "ctrl+shift+f",
        "ctrl+shift+c",
        "ctrl+shift+b",
        "ctrl+shift+r",
    ]
    assert [profile.temperature for profile in profiles] == [0.1, 0.4, 0.3, 0.3, 0.5]
    assert all(profile.paste for profile in profiles)
    assert all(profile.prompt for profile in profiles)


def test_profiles_are_addressable_and_hotkeys_are_normalized():
    config = default_config()
    assert config.profile_by_name("GRAMMAR").hotkey == "ctrl+shift+g"
    assert config.profile_by_hotkey("ctrl+shift+r").name == "reply"
    assert config.profile_by_name("missing") is None
    assert config.profile_by_hotkey("ctrl+alt+z") is None

    parsed = loads(
        """
        [[profile]]
        name = "grammar"
        hotkey = "Shift + CONTROL+G"
        prompt = "Fix it."
        """
    )
    assert parsed.profiles[0].hotkey == "ctrl+shift+g"


def test_invalid_values_are_rejected_by_key():
    with pytest.raises(ConfigError, match="at least one"):
        loads("[api]\nmodel = 'claude-haiku-4-5-20251001'\n")

    with pytest.raises(ConfigError, match="timeout_seconds"):
        loads("[api]\ntimeout_seconds = 9000\n" + MINIMAL_PROFILE)

    with pytest.raises(ConfigError, match="restore_clipboard"):
        loads('[behavior]\nrestore_clipboard = "yes"\n' + MINIMAL_PROFILE)

    with pytest.raises(ConfigError, match="temperature"):
        loads(MINIMAL_PROFILE + "temperature = 3.0\n")

    with pytest.raises(ConfigError, match=r"profile\[0\].hotkey"):
        loads(MINIMAL_PROFILE.replace("ctrl+shift+g", "ctrl+shift+notakey"))

    with pytest.raises(ConfigError, match="invalid TOML"):
        loads("[api\nmodel = 'x'")


def test_duplicate_names_and_hotkeys_are_rejected():
    with pytest.raises(ConfigError, match="duplicate profile name"):
        loads(
            MINIMAL_PROFILE
            + """
            [[profile]]
            name = "Grammar"
            hotkey = "ctrl+shift+h"
            prompt = "Fix it again."
            """
        )

    # The same combination written differently is still the same hotkey.
    with pytest.raises(ConfigError, match="is used by both"):
        loads(
            MINIMAL_PROFILE
            + """
            [[profile]]
            name = "formal"
            hotkey = "shift+ctrl+g"
            prompt = "Formalize it."
            """
        )


def test_the_default_file_is_created_once_and_never_overwritten(isolated_home):
    path = write_default_config()
    assert path == isolated_home / "config.toml"
    assert path.exists()

    edited = "# edited by the user\n" + path.read_text(encoding="utf-8")
    path.write_text(edited, encoding="utf-8")
    write_default_config()
    assert path.read_text(encoding="utf-8").startswith("# edited by the user")

    config = load_config()
    assert isinstance(config, Config)
    assert len(config.profiles) == 5


def test_configuration_round_trips_through_toml():
    original = default_config()
    assert loads(dump_config(original)) == original


def test_the_watcher_reloads_valid_edits_and_keeps_the_last_good_config(isolated_home):
    path = write_default_config()
    errors: list[Exception] = []
    reloads: list[Config] = []
    watcher = ConfigWatcher(path, on_reload=reloads.append, on_error=errors.append)
    assert watcher.config.api.timeout_seconds == 30

    path.write_text(
        config_module.DEFAULT_CONFIG_TOML.replace(
            "timeout_seconds = 30", "timeout_seconds = 45"
        ),
        encoding="utf-8",
    )
    assert watcher.reload() is True
    assert watcher.config.api.timeout_seconds == 45
    assert len(reloads) == 1

    # A broken edit must not take down a running application.
    path.write_text("this is not valid toml [[[", encoding="utf-8")
    assert watcher.reload() is False
    assert watcher.config.api.timeout_seconds == 45
    assert len(errors) == 1

    # Saving without an effective change is not reported as a reload.
    path.write_text(
        config_module.DEFAULT_CONFIG_TOML.replace(
            "timeout_seconds = 30", "timeout_seconds = 45"
        ),
        encoding="utf-8",
    )
    assert watcher.reload() is False
    assert len(reloads) == 1
