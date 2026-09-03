"""Default TOML, validation, and live reload.

The configuration file is the only supported way to change behavior, so it is
validated strictly and reloaded while the application runs. A bad edit must
never take down a running Pedantic: :class:`ConfigWatcher` keeps serving the
last known-good configuration and reports the error instead.
"""

from __future__ import annotations

import logging
import threading
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

import tomli_w

from . import paths
from .hotkeys import HotkeyError, normalize_hotkey

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-haiku-4-5-20251001"

DEFAULT_CONFIG_TOML = """\
# Pedantic configuration.
#
# This file is created once and never overwritten by an upgrade.
# Saving it makes the running application validate and reload it.

[api]
model = "claude-haiku-4-5-20251001"
timeout_seconds = 30
max_attempts = 3
max_tokens_ceiling = 2048
max_input_chars = 8000

[behavior]
paste_delay_ms = 300
clipboard_poll_timeout_ms = 700
restore_clipboard = true
notify_on_success = false
notify_on_error = true

# Optional spend guard. Zero means no limit.
[budget]
monthly_usd_limit = 0.0

[[profile]]
name = "grammar"
hotkey = "ctrl+shift+g"
paste = true
temperature = 0.1
prompt = "Fix spelling, grammar, and punctuation only. Preserve wording, tone, and register exactly. Do not rephrase for style."

[[profile]]
name = "formal"
hotkey = "ctrl+shift+f"
paste = true
temperature = 0.4
prompt = "Rewrite the text in a polished, professional register suitable for a workplace email or Teams message. Keep the original meaning and all specifics."

[[profile]]
name = "concise"
hotkey = "ctrl+shift+c"
paste = true
temperature = 0.3
prompt = "Shorten the text significantly while keeping every substantive point. Drop filler, hedges, and repetition. Do not drop facts, names, numbers, or asks."

[[profile]]
name = "bullets"
hotkey = "ctrl+shift+b"
paste = true
temperature = 0.3
prompt = "Convert the prose into a tight bulleted list. Each bullet is one idea. Keep every substantive point. Use a hyphen-plus-space bullet marker."

[[profile]]
name = "reply"
hotkey = "ctrl+shift+r"
paste = true
temperature = 0.5
prompt = "Draft a short, professional reply to the selected message. Match the sender's language. Do not invent facts, commitments, or availability that the input does not support."
"""


class ConfigError(Exception):
    """Raised when a configuration file is missing required values or invalid."""


@dataclass(frozen=True)
class ApiConfig:
    model: str = DEFAULT_MODEL
    timeout_seconds: float = 30.0
    max_attempts: int = 3
    max_tokens_ceiling: int = 2048
    max_input_chars: int = 8000


@dataclass(frozen=True)
class BehaviorConfig:
    paste_delay_ms: int = 300
    clipboard_poll_timeout_ms: int = 700
    restore_clipboard: bool = True
    notify_on_success: bool = False
    notify_on_error: bool = True


@dataclass(frozen=True)
class BudgetConfig:
    monthly_usd_limit: float = 0.0

    @property
    def enforced(self) -> bool:
        return self.monthly_usd_limit > 0


@dataclass(frozen=True)
class Profile:
    name: str
    hotkey: str
    prompt: str
    temperature: float = 0.3
    paste: bool = True


@dataclass(frozen=True)
class Config:
    api: ApiConfig = field(default_factory=ApiConfig)
    behavior: BehaviorConfig = field(default_factory=BehaviorConfig)
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    profiles: tuple[Profile, ...] = ()

    def profile_by_name(self, name: str) -> Profile | None:
        lowered = name.strip().lower()
        for profile in self.profiles:
            if profile.name.lower() == lowered:
                return profile
        return None

    def profile_by_hotkey(self, hotkey: str) -> Profile | None:
        for profile in self.profiles:
            if profile.hotkey == hotkey:
                return profile
        return None


def _require_mapping(value: Any, section: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ConfigError(f"[{section}] must be a table")
    return value


def _get_str(table: Mapping[str, Any], key: str, section: str, default: str) -> str:
    value = table.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{section}.{key} must be a non-empty string")
    return value.strip()


def _get_number(
    table: Mapping[str, Any],
    key: str,
    section: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{section}.{key} must be a number")
    if not minimum <= float(value) <= maximum:
        raise ConfigError(f"{section}.{key} must be between {minimum} and {maximum}")
    return float(value)


def _get_int(
    table: Mapping[str, Any],
    key: str,
    section: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{section}.{key} must be an integer")
    if not minimum <= value <= maximum:
        raise ConfigError(f"{section}.{key} must be between {minimum} and {maximum}")
    return value


def _get_bool(table: Mapping[str, Any], key: str, section: str, default: bool) -> bool:
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{section}.{key} must be true or false")
    return value


def parse_api(table: Mapping[str, Any]) -> ApiConfig:
    return ApiConfig(
        model=_get_str(table, "model", "api", DEFAULT_MODEL),
        timeout_seconds=_get_number(table, "timeout_seconds", "api", 30, 1, 300),
        max_attempts=_get_int(table, "max_attempts", "api", 3, 1, 10),
        max_tokens_ceiling=_get_int(table, "max_tokens_ceiling", "api", 2048, 64, 32000),
        max_input_chars=_get_int(table, "max_input_chars", "api", 8000, 100, 200000),
    )


def parse_behavior(table: Mapping[str, Any]) -> BehaviorConfig:
    return BehaviorConfig(
        paste_delay_ms=_get_int(table, "paste_delay_ms", "behavior", 300, 0, 5000),
        clipboard_poll_timeout_ms=_get_int(
            table, "clipboard_poll_timeout_ms", "behavior", 700, 100, 20000
        ),
        restore_clipboard=_get_bool(table, "restore_clipboard", "behavior", True),
        notify_on_success=_get_bool(table, "notify_on_success", "behavior", False),
        notify_on_error=_get_bool(table, "notify_on_error", "behavior", True),
    )


def parse_budget(table: Mapping[str, Any]) -> BudgetConfig:
    return BudgetConfig(
        monthly_usd_limit=_get_number(
            table, "monthly_usd_limit", "budget", 0.0, 0.0, 100000.0
        )
    )


def parse_profile(entry: Any, index: int) -> Profile:
    section = f"profile[{index}]"
    if not isinstance(entry, Mapping):
        raise ConfigError(f"{section} must be a table")
    name = _get_str(entry, "name", section, "")
    raw_hotkey = _get_str(entry, "hotkey", section, "")
    try:
        hotkey = normalize_hotkey(raw_hotkey)
    except HotkeyError as exc:
        raise ConfigError(f"{section}.hotkey is invalid: {exc}") from exc
    prompt = _get_str(entry, "prompt", section, "")
    return Profile(
        name=name,
        hotkey=hotkey,
        prompt=prompt,
        temperature=_get_number(entry, "temperature", section, 0.3, 0.0, 1.0),
        paste=_get_bool(entry, "paste", section, True),
    )


def parse_config(document: Mapping[str, Any]) -> Config:
    """Validate a parsed TOML document and build a :class:`Config`."""
    if not isinstance(document, Mapping):
        raise ConfigError("configuration must be a TOML table")

    raw_profiles = document.get("profile", [])
    if isinstance(raw_profiles, Mapping):
        raw_profiles = [raw_profiles]
    if not isinstance(raw_profiles, (list, tuple)):
        raise ConfigError("[[profile]] must be an array of tables")
    if not raw_profiles:
        raise ConfigError("at least one [[profile]] is required")

    profiles: list[Profile] = []
    seen_names: dict[str, int] = {}
    seen_hotkeys: dict[str, str] = {}
    for index, entry in enumerate(raw_profiles):
        profile = parse_profile(entry, index)
        key = profile.name.lower()
        if key in seen_names:
            raise ConfigError(f"duplicate profile name: {profile.name}")
        if profile.hotkey in seen_hotkeys:
            raise ConfigError(
                f"hotkey {profile.hotkey} is used by both "
                f"{seen_hotkeys[profile.hotkey]} and {profile.name}"
            )
        seen_names[key] = index
        seen_hotkeys[profile.hotkey] = profile.name
        profiles.append(profile)

    return Config(
        api=parse_api(_require_mapping(document.get("api"), "api")),
        behavior=parse_behavior(_require_mapping(document.get("behavior"), "behavior")),
        budget=parse_budget(_require_mapping(document.get("budget"), "budget")),
        profiles=tuple(profiles),
    )


def loads(text: str) -> Config:
    """Parse and validate configuration from TOML text."""
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML: {exc}") from exc
    return parse_config(document)


def default_config() -> Config:
    """The configuration shipped with the application."""
    return loads(DEFAULT_CONFIG_TOML)


def write_default_config(path: Path | None = None) -> Path:
    """Create the default configuration file if it does not exist.

    Returns the configuration path either way. An existing file is never
    overwritten, so upgrading the executable preserves user settings.
    """
    target = Path(path) if path else paths.config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
        log.info("created default configuration at %s", target)
    return target


def load_config(path: Path | None = None, *, create: bool = True) -> Config:
    """Load configuration from disk, creating the default file when missing."""
    target = Path(path) if path else paths.config_path()
    if create:
        write_default_config(target)
    if not target.exists():
        raise ConfigError(f"configuration file not found: {target}")
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"could not read {target}: {exc}") from exc
    try:
        return loads(text)
    except ConfigError as exc:
        raise ConfigError(f"{target}: {exc}") from exc


def dump_config(config: Config) -> str:
    """Serialize a configuration back to TOML."""
    document: dict[str, Any] = {
        "api": {
            "model": config.api.model,
            "timeout_seconds": config.api.timeout_seconds,
            "max_attempts": config.api.max_attempts,
            "max_tokens_ceiling": config.api.max_tokens_ceiling,
            "max_input_chars": config.api.max_input_chars,
        },
        "behavior": {
            "paste_delay_ms": config.behavior.paste_delay_ms,
            "clipboard_poll_timeout_ms": config.behavior.clipboard_poll_timeout_ms,
            "restore_clipboard": config.behavior.restore_clipboard,
            "notify_on_success": config.behavior.notify_on_success,
            "notify_on_error": config.behavior.notify_on_error,
        },
        "budget": {"monthly_usd_limit": config.budget.monthly_usd_limit},
        "profile": [
            {
                "name": profile.name,
                "hotkey": profile.hotkey,
                "paste": profile.paste,
                "temperature": profile.temperature,
                "prompt": profile.prompt,
            }
            for profile in config.profiles
        ],
    }
    return tomli_w.dumps(document)


class ConfigWatcher:
    """Watch the configuration file and hand out the last valid configuration.

    The directory is watched rather than the file itself because editors save by
    writing a temporary file and renaming it, which destroys the original inode
    and would silently break a file-level watch.
    """

    DEBOUNCE_SECONDS = 0.4

    def __init__(
        self,
        path: Path | None = None,
        on_reload: Callable[[Config], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self.path = Path(path) if path else paths.config_path()
        self._on_reload = on_reload
        self._on_error = on_error
        self._lock = threading.Lock()
        self._config = load_config(self.path)
        self._observer: Any = None
        self._timer: threading.Timer | None = None

    @property
    def config(self) -> Config:
        with self._lock:
            return self._config

    def set_reload_callback(self, callback: Callable[[Config], None] | None) -> None:
        """Set the reload callback after construction.

        The application object needs a valid configuration to exist before it
        can be the target of a reload, so the callback is wired in a second
        step.
        """
        self._on_reload = callback

    def start(self) -> None:
        """Begin watching. Failure to watch is not fatal."""
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except Exception:  # pragma: no cover - watchdog is a hard dependency
            log.warning("watchdog unavailable; configuration will not auto-reload")
            return

        watcher = self

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event: Any) -> None:
                if getattr(event, "is_directory", False):
                    return
                touched = {
                    str(getattr(event, "src_path", "") or ""),
                    str(getattr(event, "dest_path", "") or ""),
                }
                if any(Path(p).name == watcher.path.name for p in touched if p):
                    watcher._schedule_reload()

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._observer = Observer()
            self._observer.schedule(_Handler(), str(self.path.parent), recursive=False)
            self._observer.start()
            log.info("watching %s for changes", self.path)
        except Exception:
            log.exception("could not start configuration watcher")
            self._observer = None

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=2)
            except Exception:
                log.debug("configuration watcher did not stop cleanly", exc_info=True)
            self._observer = None

    def _schedule_reload(self) -> None:
        # Editors emit several events per save; collapse them into one reload.
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(self.DEBOUNCE_SECONDS, self.reload)
        self._timer.daemon = True
        self._timer.start()

    def reload(self) -> bool:
        """Re-read the file. Returns True when a new configuration was adopted."""
        try:
            config = load_config(self.path, create=False)
        except ConfigError as exc:
            log.error("keeping previous configuration: %s", exc)
            if self._on_error is not None:
                try:
                    self._on_error(exc)
                except Exception:
                    log.exception("configuration error callback failed")
            return False

        with self._lock:
            unchanged = config == self._config
            self._config = config
        if unchanged:
            log.debug("configuration reloaded with no effective change")
            return False
        log.info("configuration reloaded")
        if self._on_reload is not None:
            try:
                self._on_reload(config)
            except Exception:
                log.exception("configuration reload callback failed")
        return True

    def __enter__(self) -> "ConfigWatcher":
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()
