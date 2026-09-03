"""Tray icon, menu, notifications, and history UI.

Pedantic has no main window, so the tray icon is the entire user interface: it
proves the process is alive, exposes the profiles and their hotkeys, and is the
only way to change the API key or quit.

The icon is drawn at runtime with Pillow instead of shipping an image file,
which keeps the one-file executable self-contained and lets the icon reflect
state -- green when idle, amber while a transformation is running, red when the
API key is missing.

Dialogs use Tkinter from the standard library. They are created inside the
callback that shows them, on whichever thread pystray dispatched, and torn down
before returning, because a Tk object may only be touched from the thread that
created it.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from . import __version__, paths, platform as platform_actions, usage
from .credentials import (
    CredentialError,
    looks_like_api_key,
    set_api_key,
    source_description,
)
from .hotkeys import format_hotkey

log = logging.getLogger(__name__)

ICON_SIZE = 64

COLOR_IDLE = (36, 160, 84, 255)  # green: ready
COLOR_BUSY = (214, 158, 46, 255)  # amber: transforming
COLOR_ERROR = (197, 48, 48, 255)  # red: needs attention
COLOR_GLYPH = (255, 255, 255, 255)


def build_icon_image(color: tuple[int, int, int, int] = COLOR_IDLE):
    """Draw the tray icon: a white ``P`` on a colored rounded square."""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (2, 2, ICON_SIZE - 3, ICON_SIZE - 3), radius=14, fill=color
    )

    font = None
    for candidate in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            font = ImageFont.truetype(candidate, 44)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()

    box = draw.textbbox((0, 0), "P", font=font)
    x = (ICON_SIZE - (box[2] - box[0])) / 2 - box[0]
    y = (ICON_SIZE - (box[3] - box[1])) / 2 - box[1]
    draw.text((x, y), "P", font=font, fill=COLOR_GLYPH)
    return image


def prompt_for_api_key(existing_hint: str = "") -> str | None:
    """Ask for an Anthropic API key. Returns None when cancelled."""
    import tkinter as tk
    from tkinter import messagebox

    result: dict[str, str | None] = {"key": None}
    root = tk.Tk()
    root.title("Pedantic - Anthropic API key")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    message = (
        "Enter your Anthropic API key.\n\n"
        "It is stored in the Windows Credential Manager and never written to\n"
        "configuration files or logs."
    )
    if existing_hint:
        message += f"\n\nCurrent source: {existing_hint}"
    tk.Label(root, text=message, justify="left", padx=16, pady=12).pack(anchor="w")

    entry = tk.Entry(root, width=52, show="\u2022")
    entry.pack(padx=16, pady=(0, 8))
    entry.focus_set()

    def submit(_event: object = None) -> None:
        value = entry.get().strip()
        if not value:
            messagebox.showwarning("Pedantic", "The key is empty.", parent=root)
            return
        if not looks_like_api_key(value):
            proceed = messagebox.askyesno(
                "Pedantic",
                "That does not look like an Anthropic key (it should start "
                "with 'sk-ant-'). Use it anyway?",
                parent=root,
            )
            if not proceed:
                return
        result["key"] = value
        root.destroy()

    def cancel() -> None:
        result["key"] = None
        root.destroy()

    buttons = tk.Frame(root)
    buttons.pack(padx=16, pady=(0, 14), anchor="e")
    tk.Button(buttons, text="Save", width=10, command=submit).pack(side="left", padx=4)
    tk.Button(buttons, text="Cancel", width=10, command=cancel).pack(side="left")
    entry.bind("<Return>", submit)
    root.bind("<Escape>", lambda _event: cancel())

    root.update_idletasks()
    root.eval("tk::PlaceWindow . center")
    root.mainloop()
    return result["key"]


def show_history_window(entries: list[Any]) -> None:
    """Show recent transformations with their original and result text."""
    import tkinter as tk
    from tkinter import scrolledtext

    root = tk.Tk()
    root.title("Pedantic - history")
    root.geometry("760x520")

    if not entries:
        tk.Label(root, text="No transformations recorded yet.", padx=20, pady=20).pack()
        root.mainloop()
        return

    listbox = tk.Listbox(root, height=8, activestyle="dotbox")
    listbox.pack(fill="x", padx=10, pady=(10, 4))
    for entry in entries:
        listbox.insert("end", entry.label)

    detail = scrolledtext.ScrolledText(root, wrap="word", height=16)
    detail.pack(fill="both", expand=True, padx=10, pady=(4, 4))

    def show(_event: object = None) -> None:
        selection = listbox.curselection()
        if not selection:
            return
        entry = entries[selection[0]]
        detail.delete("1.0", "end")
        detail.insert(
            "end",
            f"{entry.timestamp}  {entry.profile}  {entry.model}\n"
            f"{entry.input_tokens} in / {entry.output_tokens} out tokens\n\n"
            f"--- original ---\n{entry.original}\n\n--- result ---\n{entry.result}\n",
        )

    def copy_result() -> None:
        selection = listbox.curselection()
        if not selection:
            return
        root.clipboard_clear()
        root.clipboard_append(entries[selection[0]].result)

    listbox.bind("<<ListboxSelect>>", show)
    tk.Button(root, text="Copy result", command=copy_result).pack(
        anchor="e", padx=10, pady=(0, 10)
    )
    listbox.selection_set(0)
    show()
    root.mainloop()


def show_message(title: str, message: str) -> None:
    """Show a plain informational dialog."""
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    messagebox.showinfo(title, message, parent=root)
    root.destroy()


class Tray:
    """The pystray icon and its menu.

    Long-running actions and every dialog run on their own thread. pystray
    dispatches menu callbacks on the thread running the Windows message loop,
    and blocking that thread freezes the icon for the whole desktop shell.
    """

    def __init__(
        self,
        app: Any,
        on_exit: Callable[[], None] | None = None,
        icon_factory: Callable[..., Any] = build_icon_image,
    ) -> None:
        self.app = app
        self._on_exit = on_exit
        self._icon_factory = icon_factory
        self._icon: Any = None
        self._busy = False
        self._lock = threading.Lock()

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _spawn(target: Callable[[], None], name: str) -> None:
        threading.Thread(target=target, name=name, daemon=True).start()

    def _run_guarded(self, action: Callable[[], None], name: str) -> None:
        def wrapper() -> None:
            try:
                action()
            except Exception:
                log.exception("tray action %s failed", name)

        self._spawn(wrapper, f"pedantic-{name}")

    def _color(self) -> tuple[int, int, int, int]:
        if self._busy:
            return COLOR_BUSY
        from .credentials import has_api_key

        try:
            return COLOR_IDLE if has_api_key() else COLOR_ERROR
        except Exception:
            return COLOR_IDLE

    def refresh_icon(self) -> None:
        if self._icon is None:
            return
        try:
            self._icon.icon = self._icon_factory(self._color())
        except Exception:
            log.debug("could not refresh the tray icon", exc_info=True)

    def set_busy(self, busy: bool) -> None:
        """Tint the icon while a transformation runs."""
        with self._lock:
            if self._busy == busy:
                return
            self._busy = busy
        self.refresh_icon()

    def notify(self, title: str, message: str, level: str = "info") -> None:
        """Show a balloon notification, silently doing nothing if unsupported."""
        icon = self._icon
        if icon is None:
            return
        try:
            if not getattr(icon, "HAS_NOTIFICATION", False):
                return
            icon.notify(message, title)
        except Exception:
            log.debug("could not show a notification", exc_info=True)

    # -- menu actions ----------------------------------------------------

    def action_set_api_key(self) -> None:
        def work() -> None:
            key = prompt_for_api_key(source_description())
            if not key:
                return
            try:
                set_api_key(key)
            except CredentialError as exc:
                show_message("Pedantic", f"Could not store the key:\n{exc}")
                return
            self.app.reset_backend()
            self.refresh_icon()
            show_message(
                "Pedantic",
                "API key saved to the Windows Credential Manager.",
            )

        self._run_guarded(work, "api-key")

    def action_validate_api_key(self) -> None:
        def work() -> None:
            try:
                backend = self.app.get_backend()
                valid = backend.validate_credentials()
            except Exception as exc:
                show_message("Pedantic", f"Could not validate the key:\n{exc}")
                return
            show_message(
                "Pedantic",
                "The API key works." if valid else "The API key was rejected.",
            )

        self._run_guarded(work, "validate-key")

    def action_show_history(self) -> None:
        self._run_guarded(
            lambda: show_history_window(self.app.history.latest(25)), "history"
        )

    def action_clear_history(self) -> None:
        self._run_guarded(self.app.history.clear, "clear-history")

    def action_show_usage(self) -> None:
        def work() -> None:
            summary = usage.month_summary()
            limit = self.app.current_config.budget.monthly_usd_limit
            text = summary.describe()
            if limit > 0:
                text += f"\nMonthly limit: ${limit:.2f}"
            show_message("Pedantic - usage", text)

        self._run_guarded(work, "usage")

    def action_open_config(self) -> None:
        self._run_guarded(lambda: platform_actions.open_path(paths.config_path()), "config")

    def action_open_log(self) -> None:
        self._run_guarded(lambda: platform_actions.open_path(paths.log_path()), "log")

    def action_open_data_folder(self) -> None:
        self._run_guarded(lambda: platform_actions.open_path(paths.data_dir()), "folder")

    def action_reload_config(self) -> None:
        def work() -> None:
            watcher = getattr(self.app, "watcher", None)
            if watcher is None:
                show_message("Pedantic", "Configuration reloading is not active.")
                return
            watcher.reload()
            self.app.apply_config(watcher.config)

        self._run_guarded(work, "reload")

    def action_toggle_startup(self) -> None:
        def work() -> None:
            enabled = platform_actions.is_start_with_windows_enabled()
            platform_actions.set_start_with_windows(not enabled)
            if self._icon is not None:
                self._icon.update_menu()

        self._run_guarded(work, "startup")

    def action_exit(self) -> None:
        log.info("exiting from the tray menu")
        icon = self._icon
        if icon is not None:
            icon.visible = False
            icon.stop()
        if self._on_exit is not None:
            self._run_guarded(self._on_exit, "exit")

    # -- menu ------------------------------------------------------------

    def build_menu(self) -> Any:
        import pystray

        item = pystray.MenuItem
        separator = pystray.Menu.SEPARATOR

        profile_items = tuple(
            item(f"{profile.name}   {format_hotkey(profile.hotkey)}", None, enabled=False)
            for profile in self.app.current_config.profiles
        )

        return pystray.Menu(
            item(f"Pedantic {__version__}", None, enabled=False),
            separator,
            item("Profiles", pystray.Menu(*profile_items)),
            item(
                "History",
                pystray.Menu(
                    item("Show history\u2026", lambda: self.action_show_history()),
                    item("Clear history", lambda: self.action_clear_history()),
                ),
            ),
            item("Usage this month\u2026", lambda: self.action_show_usage()),
            separator,
            item("Set API key\u2026", lambda: self.action_set_api_key()),
            item("Test API key\u2026", lambda: self.action_validate_api_key()),
            separator,
            item("Open config file", lambda: self.action_open_config()),
            item("Open log file", lambda: self.action_open_log()),
            item("Open data folder", lambda: self.action_open_data_folder()),
            item("Reload configuration", lambda: self.action_reload_config()),
            item(
                "Start with Windows",
                lambda: self.action_toggle_startup(),
                checked=lambda _item: platform_actions.is_start_with_windows_enabled(),
            ),
            separator,
            item("Exit", lambda: self.action_exit()),
        )

    def create(self) -> Any:
        import pystray

        self._icon = pystray.Icon(
            "Pedantic",
            icon=self._icon_factory(self._color()),
            title=f"Pedantic {__version__}",
            menu=self.build_menu(),
        )
        return self._icon

    def rebuild_menu(self) -> None:
        """Rebuild the menu after the profile list changed."""
        if self._icon is None:
            return
        try:
            self._icon.menu = self.build_menu()
            self._icon.update_menu()
        except Exception:
            log.debug("could not rebuild the tray menu", exc_info=True)

    def run(self) -> None:
        """Show the icon and run the message loop. Blocks until Exit."""
        if self._icon is None:
            self.create()
        log.info("tray icon started")
        self._icon.run()
        log.info("tray icon stopped")

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.visible = False
                self._icon.stop()
            except Exception:
                log.debug("could not stop the tray icon", exc_info=True)
