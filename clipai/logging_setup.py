"""Rotating logging and secret redaction.

The log is the primary support tool for a tray application with no window, so
it is always on and always in a known location. Two constraints shape it:

*It must not grow without bound* on a machine that runs for months, hence
rotation with a small number of small files.

*It must be safe to send to someone else.* A redacting filter runs over every
record so an API key that reaches a log line through an exception message or a
misplaced debug call is replaced before it is written.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from . import paths
from .credentials import redact

MAX_BYTES = 1_000_000
BACKUP_COUNT = 3
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)-22s %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


class RedactingFilter(logging.Filter):
    """Strip Anthropic-looking keys from messages, arguments, and exceptions."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    key: redact(value) if isinstance(value, str) else value
                    for key, value in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    redact(value) if isinstance(value, str) else value
                    for value in record.args
                )
        return True


class RedactingFormatter(logging.Formatter):
    """Redact the fully rendered line, including formatted tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def configure_logging(
    debug: bool = False, log_file: Path | None = None, console: bool | None = None
) -> Path | None:
    """Install the rotating file handler and return the log path.

    Safe to call more than once; later calls only adjust the level. Returns
    None when no log file could be opened, in which case logging still works
    but only to the console.
    """
    global _configured

    level = logging.DEBUG if debug else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)

    if _configured:
        for handler in root.handlers:
            handler.setLevel(level)
        return log_file or paths.log_path()

    formatter = RedactingFormatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    redacting = RedactingFilter()

    target = Path(log_file) if log_file else paths.log_path()
    resolved: Path | None = target
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            target,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
            delay=True,
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(redacting)
        file_handler.setLevel(level)
        root.addHandler(file_handler)
    except OSError:
        resolved = None

    # A frozen windowed build has no console; writing to a missing stream would
    # raise inside logging itself.
    if console is None:
        console = not paths.is_frozen() and sys.stderr is not None
    if console:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        stream_handler.addFilter(redacting)
        stream_handler.setLevel(level)
        root.addHandler(stream_handler)

    # These libraries log request-level detail that is noise here.
    for noisy in ("httpx", "httpx2", "httpcore", "httpcore2", "anthropic", "watchdog"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True
    if resolved is None:
        logging.getLogger(__name__).warning("could not open a log file at %s", target)
    return resolved


def reset_logging() -> None:
    """Remove installed handlers. Used by tests."""
    global _configured
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    _configured = False
