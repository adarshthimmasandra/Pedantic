"""Single-instance lock and OS actions.

Two Pedantic processes sharing one configuration directory would both bind the
same global hotkeys and both try to drive the clipboard, so startup takes an
advisory lock on a file inside that directory.

The lock is held as a byte range on an open file rather than as a
"does the file exist" check. Windows releases file locks when a process dies,
so a crash or a hard power-off cannot leave a stale lock that blocks every
future launch.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from . import paths

log = logging.getLogger(__name__)

RUN_REGISTRY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "Pedantic"

# The lock is taken on a byte past the end of the text the file holds. Locking
# byte zero would also make the recorded pid unreadable to anyone else, and the
# pid is the only clue about which process is holding the lock.
LOCK_BYTE_OFFSET = 4096


class AlreadyRunning(Exception):
    """Raised when another instance already holds the lock."""


class SingleInstanceLock:
    """An advisory lock scoped to one configuration directory."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else paths.lock_path()
        self._handle = None

    def acquire(self) -> bool:
        """Try to take the lock. Returns False when another instance holds it."""
        if self._handle is not None:
            return True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(self.path, "a+", encoding="utf-8")
        except OSError as exc:
            # An unwritable data directory should not prevent starting up.
            log.warning("could not open the lock file %s: %s", self.path, exc)
            return True

        if not self._lock_handle(handle):
            handle.close()
            return False

        self._handle = handle
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(f"{os.getpid()}\n")
            handle.flush()
        except OSError:
            log.debug("could not write the pid into the lock file", exc_info=True)
        log.debug("acquired the single-instance lock at %s", self.path)
        return True

    @staticmethod
    def _lock_handle(handle) -> bool:
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(LOCK_BYTE_OFFSET)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - Windows is the supported platform
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def release(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(LOCK_BYTE_OFFSET)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            log.debug("could not release the lock cleanly", exc_info=True)
        finally:
            handle.close()
            log.debug("released the single-instance lock")

    def other_pid(self) -> int | None:
        """The pid recorded by the instance holding the lock, if readable."""
        try:
            return int(self.path.read_text(encoding="utf-8").strip().splitlines()[0])
        except (OSError, ValueError, IndexError):
            return None

    def __enter__(self) -> "SingleInstanceLock":
        if not self.acquire():
            pid = self.other_pid()
            suffix = f" (pid {pid})" if pid else ""
            raise AlreadyRunning(f"Pedantic is already running{suffix}")
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()


def open_path(target: Path | str) -> bool:
    """Open a file or folder with the shell's default handler."""
    path = Path(target)
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606 - shell open is the intent
        else:  # pragma: no cover
            subprocess.Popen(["xdg-open", str(path)])
        return True
    except OSError as exc:
        log.warning("could not open %s: %s", path, exc)
        return False


def open_url(url: str) -> bool:
    import webbrowser

    try:
        return webbrowser.open(url)
    except Exception as exc:
        log.warning("could not open %s: %s", url, exc)
        return False


def reveal_in_explorer(target: Path | str) -> bool:
    """Open Explorer with the given file selected."""
    path = Path(target)
    if sys.platform != "win32":  # pragma: no cover
        return open_path(path.parent)
    try:
        subprocess.Popen(["explorer", f"/select,{path}"])
        return True
    except OSError as exc:
        log.warning("could not reveal %s: %s", path, exc)
        return False


def is_elevated() -> bool:
    """True when this process runs elevated.

    Windows blocks a non-elevated process from injecting input into an elevated
    one, so this is reported in diagnostics to explain a hotkey that does
    nothing in one specific application.
    """
    if sys.platform != "win32":  # pragma: no cover
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def startup_command() -> str:
    """The command a startup entry should run."""
    if paths.is_frozen():
        return f'"{sys.executable}"'
    return f'"{sys.executable}" -m clipai'


def is_start_with_windows_enabled() -> bool:
    if sys.platform != "win32":  # pragma: no cover
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_REGISTRY_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE_NAME)
            return bool(value)
    except FileNotFoundError:
        return False
    except OSError:
        log.debug("could not read the Run registry key", exc_info=True)
        return False


def set_start_with_windows(enabled: bool) -> bool:
    """Add or remove the per-user startup entry. Returns the resulting state."""
    if sys.platform != "win32":  # pragma: no cover
        return False
    import winreg

    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_REGISTRY_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(
                    key, RUN_VALUE_NAME, 0, winreg.REG_SZ, startup_command()
                )
                log.info("enabled start with Windows")
            else:
                try:
                    winreg.DeleteValue(key, RUN_VALUE_NAME)
                    log.info("disabled start with Windows")
                except FileNotFoundError:
                    pass
    except OSError as exc:
        log.warning("could not change the startup entry: %s", exc)
        return is_start_with_windows_enabled()
    return enabled
