"""Hotkey-to-copy-to-API-to-paste orchestration.

One transformation runs as a strict sequence, and each step exists because of a
specific failure it prevents:

1. The hotkey is detected and suppressed, so the foreground application never
   acts on it.
2. The physical modifiers are allowed to come up first. Injecting ``Ctrl+C``
   while the user still holds ``Shift`` produces ``Ctrl+Shift+C``.
3. The clipboard is snapshotted, including its sequence number.
4. A clean ``Ctrl+C`` is injected.
5. The sequence number is polled. Comparing clipboard *text* would not detect
   copying the same text twice, and would also mistake a stale clipboard for a
   successful copy.
6. The new text is read, validated, and sanitized.
7. The model transforms it, the response is cleaned, and the result is placed
   on the clipboard and pasted.
8. The original clipboard content is restored after a short delay, because the
   target application reads the clipboard asynchronously and restoring
   immediately would paste the old content.

Only one transformation is allowed at a time. A second hotkey press while one is
in flight is rejected rather than queued, because both would fight over the one
system clipboard.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from . import cleaning, clipboard as clipboard_module, keys as keys_module
from . import prompts, usage
from .backends.base import Backend, BackendError, TransformRequest, size_max_tokens
from .config import Config, ConfigWatcher, Profile
from .credentials import get_api_key
from .history import History
from .hotkeys import HotkeyListener, format_hotkey
from .selection import describe_foreground
from .usage import BudgetExceeded

log = logging.getLogger(__name__)

# Time for the target application to process the injected paste before the
# clipboard is put back the way the user left it.
CLIPBOARD_SETTLE_SECONDS = 0.05

MSG_NO_KEY = "No Anthropic API key is configured. Set it from the tray menu."
MSG_COPY_FAILED = (
    "Could not copy selected text. Select text first, and make sure Pedantic "
    "and the target application run at the same privilege level."
)
MSG_NO_SELECTION = "No text was selected."
MSG_IN_FLIGHT = "transform already in flight"


class TransformBusy(Exception):
    """Raised when a transformation is already running."""


@dataclass(frozen=True)
class TransformOutcome:
    """The result of one hotkey press, successful or not."""

    profile: str
    ok: bool
    message: str = ""
    result: str | None = None
    original: str | None = None
    pasted: bool = False
    restored: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


@dataclass
class PedanticApp:
    """Wires hotkeys, the clipboard, the backend, history, and usage together.

    Every collaborator is injectable so the whole pipeline can be exercised in
    tests without a keyboard, a clipboard, or a network.
    """

    config: Config | None = None
    watcher: ConfigWatcher | None = None
    backend: Backend | None = None
    backend_factory: Callable[[Config], Backend] | None = None
    notifier: Callable[[str, str, str], None] | None = None
    history: History | None = None
    clipboard: Any = clipboard_module
    keys: Any = keys_module
    usage_recorder: Any = usage
    sleep: Callable[[float], None] = time.sleep
    debug: bool = False
    listener: HotkeyListener | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _stopping: threading.Event = field(default_factory=threading.Event, repr=False)

    def __post_init__(self) -> None:
        if self.config is None:
            if self.watcher is not None:
                self.config = self.watcher.config
            else:
                from .config import default_config

                self.config = default_config()
        if self.history is None:
            self.history = History()

    # -- configuration ---------------------------------------------------

    @property
    def current_config(self) -> Config:
        if self.watcher is not None:
            return self.watcher.config
        assert self.config is not None
        return self.config

    def bindings(self, config: Config | None = None) -> dict[str, str]:
        """Map canonical hotkey to profile name."""
        active = config or self.current_config
        return {profile.hotkey: profile.name for profile in active.profiles}

    def apply_config(self, config: Config) -> None:
        """Adopt a reloaded configuration.

        The backend is dropped so a changed timeout or model takes effect, and
        the hotkey table is replaced without restarting the keyboard hook.
        """
        self.config = config
        if self.listener is not None:
            self.listener.set_bindings(self.bindings(config))
        self.reset_backend()
        log.info("applied reloaded configuration")

    def reset_backend(self) -> None:
        backend = self.backend
        self.backend = None
        if backend is not None:
            try:
                backend.close()
            except Exception:
                log.debug("could not close the previous backend", exc_info=True)

    def get_backend(self) -> Backend:
        """Build the backend on first use so startup never blocks on the key."""
        if self.backend is not None:
            return self.backend
        config = self.current_config
        if self.backend_factory is not None:
            self.backend = self.backend_factory(config)
            return self.backend

        api_key = get_api_key()
        if not api_key:
            raise BackendError(MSG_NO_KEY)
        from .backends.anthropic_api import AnthropicBackend
        from .backends.retry import RetryPolicy

        self.backend = AnthropicBackend(
            api_key=api_key,
            timeout=config.api.timeout_seconds,
            policy=RetryPolicy(max_attempts=config.api.max_attempts),
        )
        return self.backend

    # -- notifications ---------------------------------------------------

    def notify(self, title: str, message: str, level: str = "info") -> None:
        behavior = self.current_config.behavior
        if level == "error" and not behavior.notify_on_error:
            return
        if level == "success" and not behavior.notify_on_success:
            return
        if self.notifier is None:
            return
        try:
            self.notifier(title, message, level)
        except Exception:
            log.exception("notification failed")

    # -- the transformation pipeline -------------------------------------

    def handle_hotkey(self, hotkey: str) -> TransformOutcome | None:
        """Entry point called by the hotkey listener."""
        profile = self.current_config.profile_by_hotkey(hotkey)
        if profile is None:
            log.debug("no profile bound to %s", hotkey)
            return None
        return self.run_profile(profile)

    def run_profile(self, profile: Profile) -> TransformOutcome:
        """Run one transformation, rejecting overlap."""
        if not self._lock.acquire(blocking=False):
            log.info("%s: %s", profile.name, MSG_IN_FLIGHT)
            return TransformOutcome(
                profile=profile.name, ok=False, message="A transformation is already running."
            )
        try:
            return self._run_locked(profile)
        finally:
            self._lock.release()

    def _run_locked(self, profile: Profile) -> TransformOutcome:
        config = self.current_config
        started = time.monotonic()
        log.info(
            "%s triggered by %s in %s",
            profile.name,
            format_hotkey(profile.hotkey),
            describe_foreground().describe() if self.debug else "foreground window",
        )

        try:
            self.usage_recorder.check_budget(config.budget.monthly_usd_limit)
        except BudgetExceeded as exc:
            log.warning("%s", exc)
            self.notify("Pedantic", str(exc), "error")
            return TransformOutcome(profile=profile.name, ok=False, message=str(exc))

        # Step 3: remember what the clipboard held before we touch it.
        snapshot = self.clipboard.snapshot()

        # Step 2: a clean Ctrl+C needs the user's modifiers released first.
        self.keys.wait_for_modifier_release()

        # Steps 4-6: copy and read the selection.
        try:
            captured = self.clipboard.copy_and_read(
                snapshot.sequence,
                config.behavior.clipboard_poll_timeout_ms,
                send_copy=self.keys.send_ctrl_c,
            )
        except Exception as exc:
            log.error("copy failed: %s", exc)
            self.notify("Pedantic", MSG_COPY_FAILED, "error")
            return TransformOutcome(
                profile=profile.name, ok=False, message=MSG_COPY_FAILED
            )

        if captured is None:
            log.warning("no clipboard change after the synthetic ctrl+c")
            self.notify("Pedantic", MSG_COPY_FAILED, "error")
            return TransformOutcome(
                profile=profile.name, ok=False, message=MSG_COPY_FAILED
            )

        # Step 7: validate and sanitize.
        original = cleaning.sanitize_input(captured)
        if not original or not cleaning.has_transformable_text(original):
            log.info("captured text had nothing to transform")
            self.clipboard.restore(snapshot)
            self.notify("Pedantic", MSG_NO_SELECTION, "error")
            return TransformOutcome(
                profile=profile.name, ok=False, message=MSG_NO_SELECTION
            )

        limit = config.api.max_input_chars
        if len(original) > limit:
            message = (
                f"Selected text is too long: {len(original):,} characters, "
                f"limit {limit:,}. Select less text or raise max_input_chars."
            )
            log.warning("%s", message)
            self.clipboard.restore(snapshot)
            self.notify("Pedantic", message, "error")
            return TransformOutcome(profile=profile.name, ok=False, message=message)

        if self.debug:
            log.debug("captured text: %s", original)
        else:
            log.info("captured %d characters: %s", len(original), cleaning.preview(original))

        # Step 8: transform.
        try:
            result = self._call_backend(profile, original, config)
        except BackendError as exc:
            log.error("%s failed: %s", profile.name, exc)
            self.clipboard.restore(snapshot)
            self.notify("Pedantic", str(exc), "error")
            return TransformOutcome(profile=profile.name, ok=False, message=str(exc))
        except Exception as exc:
            log.exception("%s failed unexpectedly", profile.name)
            self.clipboard.restore(snapshot)
            message = f"The AI request failed: {exc}"
            self.notify("Pedantic", message, "error")
            return TransformOutcome(profile=profile.name, ok=False, message=message)

        # Step 9: clean the response.
        cleaned = cleaning.clean_output(result.text)
        if not cleaned:
            self.clipboard.restore(snapshot)
            message = "The AI returned an empty response."
            self.notify("Pedantic", message, "error")
            return TransformOutcome(profile=profile.name, ok=False, message=message)

        # Steps 10-12: paste, then put the clipboard back.
        pasted, restored = self._deliver(cleaned, snapshot, profile, config)

        self._record(profile, original, cleaned, result)
        elapsed = time.monotonic() - started
        log.info(
            "%s completed in %.2fs (%d in / %d out tokens, %d attempt(s))",
            profile.name,
            elapsed,
            result.input_tokens,
            result.output_tokens,
            result.attempts,
        )
        self.notify("Pedantic", cleaning.preview(cleaned), "success")
        return TransformOutcome(
            profile=profile.name,
            ok=True,
            message=cleaning.preview(cleaned),
            result=cleaned,
            original=original,
            pasted=pasted,
            restored=restored,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            model=result.model,
        )

    def _call_backend(self, profile: Profile, text: str, config: Config):
        request = TransformRequest(
            text=prompts.wrap_user_text(text),
            system_prompt=prompts.build_system_prompt(profile.prompt),
            temperature=profile.temperature,
            model=config.api.model,
            max_tokens=size_max_tokens(text, config.api.max_tokens_ceiling),
        )
        return self.get_backend().transform(request)

    def _deliver(
        self,
        text: str,
        snapshot: Any,
        profile: Profile,
        config: Config,
    ) -> tuple[bool, bool]:
        """Place the result on the clipboard, paste it, and restore."""
        try:
            self.clipboard.write_text(text)
        except Exception as exc:
            log.error("could not place the result on the clipboard: %s", exc)
            self.notify("Pedantic", f"Could not write to the clipboard: {exc}", "error")
            return False, False

        if not profile.paste:
            log.info("%s: paste disabled; the result is on the clipboard", profile.name)
            self.notify(
                "Pedantic", "Result copied to the clipboard.", "success"
            )
            return False, False

        pasted = True
        try:
            self.sleep(CLIPBOARD_SETTLE_SECONDS)
            self.keys.send_ctrl_v()
        except Exception as exc:
            log.error("could not paste: %s", exc)
            self.notify(
                "Pedantic",
                f"Could not paste automatically; the result is on the clipboard. ({exc})",
                "error",
            )
            pasted = False

        restored = False
        if config.behavior.restore_clipboard:
            # The target application reads the clipboard asynchronously, so the
            # restore has to wait for the paste to actually land.
            self.sleep(config.behavior.paste_delay_ms / 1000.0)
            restored = bool(self.clipboard.restore(snapshot))
        return pasted, restored

    def _record(self, profile: Profile, original: str, result: str, transform) -> None:
        config = self.current_config
        try:
            assert self.history is not None
            self.history.record(
                profile=profile.name,
                original=original,
                result=result,
                model=transform.model or config.api.model,
                input_tokens=transform.input_tokens,
                output_tokens=transform.output_tokens,
            )
        except Exception:
            log.debug("could not write history", exc_info=True)
        try:
            self.usage_recorder.record_usage(
                profile=profile.name,
                model=transform.model or config.api.model,
                input_tokens=transform.input_tokens,
                output_tokens=transform.output_tokens,
            )
        except Exception:
            log.debug("could not record usage", exc_info=True)

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        """Start the hotkey listener."""
        if self.listener is None:
            self.listener = HotkeyListener(
                callback=self.handle_hotkey, bindings=self.bindings()
            )
        else:
            self.listener.set_bindings(self.bindings())
        self.listener.start()
        for profile in self.current_config.profiles:
            log.info("%s -> %s", format_hotkey(profile.hotkey), profile.name)

    def stop(self) -> None:
        self._stopping.set()
        if self.listener is not None:
            self.listener.stop()
        self.reset_backend()

    @property
    def stopping(self) -> bool:
        return self._stopping.is_set()
