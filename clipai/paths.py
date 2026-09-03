"""Per-user data locations.

Everything Pedantic writes at runtime lives in a single per-user directory so
that uninstalling the executable never touches user data, and so that the
single-instance lock can be scoped to one configuration directory.

On Windows the directory is ``%APPDATA%\\clipai``. The environment variable
``CLIPAI_HOME`` overrides it, which keeps tests and portable setups isolated.
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
from pathlib import Path

HOME_ENV_VAR = "CLIPAI_HOME"
DIR_NAME = "clipai"

CONFIG_FILE_NAME = "config.toml"
LOG_FILE_NAME = "clipai.log"
HISTORY_FILE_NAME = "history.json"
LOCK_FILE_NAME = "clipai.lock"
USAGE_FILE_PREFIX = "usage-"


def _default_data_dir() -> Path:
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / DIR_NAME
        return Path.home() / "AppData" / "Roaming" / DIR_NAME
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / DIR_NAME
    return Path.home() / ".config" / DIR_NAME


def data_dir() -> Path:
    """Return the per-user data directory without creating it."""
    override = os.environ.get(HOME_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return _default_data_dir()


def ensure_data_dir() -> Path:
    """Return the per-user data directory, creating it if necessary."""
    directory = data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def config_path() -> Path:
    return data_dir() / CONFIG_FILE_NAME


def log_path() -> Path:
    return data_dir() / LOG_FILE_NAME


def history_path() -> Path:
    return data_dir() / HISTORY_FILE_NAME


def lock_path() -> Path:
    return data_dir() / LOCK_FILE_NAME


def usage_path(when: _dt.date | None = None) -> Path:
    """Return the usage journal for a month, e.g. ``usage-2026-09.jsonl``."""
    moment = when or _dt.date.today()
    return data_dir() / f"{USAGE_FILE_PREFIX}{moment:%Y-%m}.jsonl"


def usage_files() -> list[Path]:
    directory = data_dir()
    if not directory.is_dir():
        return []
    return sorted(directory.glob(f"{USAGE_FILE_PREFIX}*.jsonl"))


def is_frozen() -> bool:
    """True when running from a PyInstaller one-file executable."""
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """Directory holding bundled resources, or the package directory."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def executable_path() -> Path:
    """Path used to relaunch the application, e.g. for startup shortcuts."""
    if is_frozen():
        return Path(sys.executable)
    return Path(sys.executable)
