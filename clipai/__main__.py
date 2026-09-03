"""CLI entry point and startup sequence.

Startup is ordered so that a failure reports itself in the most useful way
available at that moment:

1. Parse arguments and configure logging, so everything after this point is
   recorded in the log file.
2. Answer informational flags and exit without touching any state.
3. Take the single-instance lock before binding hotkeys, so a second launch
   cannot steal them from the running instance.
4. Load or create the configuration.
5. Make sure an API key exists, prompting for one on first launch.
6. Start the hotkey listener and the configuration watcher, then hand the main
   thread to the tray icon's message loop.
"""

from __future__ import annotations

import argparse
import logging
import sys

# Absolute imports on purpose: PyInstaller runs this file as a top-level
# script named __main__, so relative imports have no parent package to resolve
# against. Absolute imports work both frozen and under `python -m clipai`.
from clipai import __version__, paths
from clipai.config import ConfigError, ConfigWatcher, write_default_config
from clipai.credentials import CredentialError, get_api_key, set_api_key
from clipai.logging_setup import configure_logging
from clipai.platform import AlreadyRunning, SingleInstanceLock

log = logging.getLogger(__name__)

PROGRAM = "clipai"


def _attach_console() -> None:
    """Attach to the parent console so a windowed build can print.

    A PyInstaller ``console=False`` build has no standard streams. Reattaching
    to the console that launched it is what makes ``Pedantic.exe --version``
    show something instead of nothing.
    """
    if not paths.is_frozen() or sys.platform != "win32":
        return
    try:
        import ctypes

        ATTACH_PARENT_PROCESS = -1
        if not ctypes.windll.kernel32.AttachConsole(ATTACH_PARENT_PROCESS):
            return
        for name, stream in (("stdout", "CONOUT$"), ("stderr", "CONOUT$")):
            if getattr(sys, name, None) is None:
                try:
                    setattr(sys, name, open(stream, "w", encoding="utf-8"))
                except OSError:
                    pass
    except Exception:
        pass


def emit(text: str) -> None:
    """Print without assuming a usable stdout."""
    try:
        if sys.stdout is not None:
            print(text)
            sys.stdout.flush()
    except (OSError, ValueError, AttributeError):
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description=(
            "Pedantic transforms selected text anywhere in Windows with a "
            "global hotkey."
        ),
    )
    parser.add_argument(
        "--version", action="store_true", help="print the version and exit"
    )
    parser.add_argument(
        "--print-config-path",
        action="store_true",
        help="print the configuration file path and exit",
    )
    parser.add_argument(
        "--usage-summary",
        action="store_true",
        help="print recorded token usage and estimated cost, then exit",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="enable debug logging, which can include captured text",
    )
    return parser


def ensure_api_key(*, interactive: bool = True) -> str | None:
    """Return a usable API key, prompting on first launch.

    The key is validated against the Anthropic API before being stored, so a
    typo is caught here instead of on the first hotkey press.
    """
    key = get_api_key()
    if key:
        return key
    if not interactive:
        return None

    from clipai.tray import prompt_for_api_key, show_message

    while True:
        candidate = prompt_for_api_key()
        if not candidate:
            return None
        try:
            from clipai.backends.anthropic_api import AnthropicBackend

            backend = AnthropicBackend(api_key=candidate, timeout=20.0)
            valid = backend.validate_credentials()
            backend.close()
        except Exception as exc:
            show_message(
                "Pedantic",
                f"Could not reach the Anthropic API to check the key:\n{exc}\n\n"
                "The key will be saved anyway.",
            )
            valid = True
        if not valid:
            show_message("Pedantic", "Anthropic rejected that key. Try again.")
            continue
        try:
            set_api_key(candidate)
        except CredentialError as exc:
            show_message("Pedantic", f"Could not store the key:\n{exc}")
            return candidate
        return candidate


def run_tray(debug: bool = False) -> int:
    """Start the application and block until the user exits."""
    from clipai.app import PedanticApp
    from clipai.hotkeys import HotkeyListener
    from clipai.tray import Tray, show_message

    write_default_config()
    try:
        watcher = ConfigWatcher(on_error=lambda exc: log.error("%s", exc))
    except ConfigError as exc:
        log.error("configuration is invalid: %s", exc)
        show_message(
            "Pedantic",
            f"The configuration file could not be loaded:\n\n{exc}\n\n"
            f"Fix or delete {paths.config_path()} and start Pedantic again.",
        )
        return 2

    if ensure_api_key() is None:
        log.warning("no API key was provided; the tray menu can set one later")

    app = PedanticApp(watcher=watcher, debug=debug)
    tray = Tray(app, on_exit=app.stop)
    app.notifier = tray.notify

    def on_hotkey(hotkey: str):
        tray.set_busy(True)
        try:
            return app.handle_hotkey(hotkey)
        finally:
            tray.set_busy(False)

    app.listener = HotkeyListener(callback=on_hotkey, bindings=app.bindings())

    def on_reload(config) -> None:
        app.apply_config(config)
        tray.rebuild_menu()

    watcher.set_reload_callback(on_reload)

    try:
        app.start()
    except Exception as exc:
        log.exception("could not start the hotkey listener")
        show_message("Pedantic", f"Could not register the global hotkeys:\n{exc}")
        return 3

    watcher.start()
    try:
        tray.run()
    finally:
        watcher.stop()
        app.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _attach_console()

    if args.version:
        emit(f"{PROGRAM} {__version__}")
        return 0
    if args.print_config_path:
        emit(str(paths.config_path()))
        return 0

    paths.ensure_data_dir()
    configure_logging(debug=args.debug)

    if args.usage_summary:
        from clipai.usage import format_usage_report

        emit(format_usage_report())
        return 0

    if sys.platform != "win32":
        emit("Pedantic requires Windows.")
        return 1

    log.info("Pedantic %s starting (debug=%s)", __version__, args.debug)
    if args.debug:
        log.warning("debug logging can record captured text; do not share the log")

    lock = SingleInstanceLock()
    try:
        with lock:
            return run_tray(debug=args.debug)
    except AlreadyRunning as exc:
        log.error("%s", exc)
        emit(str(exc))
        try:
            from clipai.tray import show_message

            show_message(
                "Pedantic",
                f"{exc}.\n\nExit the running instance from the tray icon first.",
            )
        except Exception:
            pass
        return 1
    except KeyboardInterrupt:
        log.info("interrupted")
        return 0


if __name__ == "__main__":
    sys.exit(main())
