"""
AppController — owns the database, the timer, backups, and both windows.

Everything that needs to outlive a single window lives here; the widget and the
dashboard are views onto this object.
"""
import threading
from pathlib import Path
from typing import Callable, Optional

import customtkinter as ctk

from ..backup import BackupManager
from ..database import Database
from ..system import SingleInstance, data_dir, resource_path
from ..timer_engine import TimerEngine
from ..version import APP_NAME, __version__


def get_data_dir() -> Path:
    return data_dir(APP_NAME)


class AppController:
    #: How often a running timer records that the app is still watching it.
    HEARTBEAT_MS = 30_000

    def __init__(self, data_directory: Optional[Path] = None):
        self.data_dir = Path(data_directory) if data_directory else get_data_dir()
        self.db = Database(self.data_dir / "worktrack.db")
        self.timer = TimerEngine(self.db)
        self.backup = BackupManager(self.db, self.data_dir)
        self.exports_dir = self.data_dir / "exports"
        self.exports_dir.mkdir(parents=True, exist_ok=True)

        self.widget = None
        self.dashboard = None
        self._root: Optional[ctk.CTk] = None
        self._tray = None
        self._undo_stack: list[tuple[Callable, str]] = []

        threading.Thread(target=self._safe_daily_backup, daemon=True).start()

    def _safe_daily_backup(self):
        try:
            self.backup.make_daily_backup()
        except Exception:  # a failed backup must never stop the app starting
            pass

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def run(self):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # A hidden root window owns the loop; the widget and dashboard are
        # Toplevels, so either can be closed without ending the session.
        self._root = ctk.CTk()
        self._root.withdraw()
        self._root.title(f"{APP_NAME} {__version__}")
        self._root.protocol("WM_DELETE_WINDOW", self.quit)

        from .dashboard import DashboardWindow
        from .widget import WidgetWindow

        self.widget = WidgetWindow(self._root, self)
        self.dashboard = DashboardWindow(self._root, self)

        self.timer.on_change(self._on_timer_change)
        self._root.bind_all("<Control-s>", lambda _event: self._manual_save())

        threading.Thread(target=self._start_tray, daemon=True).start()
        self._root.after(self.HEARTBEAT_MS, self._heartbeat)
        self._root.after(400, self._report_recovered_session)

        self._root.mainloop()

    def _heartbeat(self):
        try:
            self.timer.heartbeat()
        except Exception:
            pass
        if self._root:
            self._root.after(self.HEARTBEAT_MS, self._heartbeat)

    def _report_recovered_session(self):
        """Tell the user when a session was closed off after an unclean exit."""
        seconds = self.timer.recovered_session_seconds
        if seconds is None or not self.dashboard:
            return
        self.timer.recovered_session_seconds = None
        from ..models import format_duration_human
        self.dashboard.notify(
            f"A timer left running was closed off at "
            f"{format_duration_human(seconds)} — the app was not open after that. "
            f"Adjust it under History if that is not right."
        )

    def quit(self):
        self._cleanup()
        if self._root:
            self._root.destroy()

    def _cleanup(self):
        if self.widget:
            try:
                self.db.set_setting("widget_x", str(self.widget.winfo_x()))
                self.db.set_setting("widget_y", str(self.widget.winfo_y()))
            except Exception:
                pass
        if self.dashboard:
            try:
                self.db.set_setting("dashboard_geometry", self.dashboard.geometry())
            except Exception:
                pass
        try:
            # A final heartbeat: if the timer is still running, a quick restart
            # should pick it up rather than close it off as abandoned.
            self.timer.heartbeat()
        except Exception:
            pass
        if self._tray:
            try:
                self._tray.stop()
            except Exception:
                pass

    # ── Cross-window updates ──────────────────────────────────────────────

    def _on_timer_change(self):
        try:
            if self.widget:
                self.widget.refresh_timer_state()
            if self.dashboard and self.dashboard.winfo_exists():
                self.dashboard.on_timer_change()
        except Exception:
            pass

    def _manual_save(self):
        # SQLite has already committed; this is the visible reassurance.
        self.db.commit()
        if self.widget:
            self.widget.flash_saved()

    def open_dashboard(self):
        if self.dashboard:
            self.dashboard.deiconify()
            self.dashboard.lift()
            self.dashboard.focus_force()

    # ── Undo ──────────────────────────────────────────────────────────────

    def push_undo(self, action: Callable, description: str = ""):
        self._undo_stack.append((action, description))
        if len(self._undo_stack) > 20:
            self._undo_stack.pop(0)

    def undo(self) -> Optional[str]:
        if not self._undo_stack:
            return None
        action, description = self._undo_stack.pop()
        try:
            action()
        except Exception:
            return None
        return description or "Undone"

    # ── System tray ───────────────────────────────────────────────────────

    def _start_tray(self):
        try:
            import pystray

            menu = pystray.Menu(
                pystray.MenuItem(f"Open {APP_NAME}", self._tray_open, default=True),
                pystray.MenuItem("Start / pause timer", self._tray_toggle_timer),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Exit", self._tray_exit),
            )
            self._tray = pystray.Icon(APP_NAME, self._tray_image(), APP_NAME, menu)
            self._tray.run()
        except Exception:
            # The tray is a convenience; the app is fully usable without it.
            self._tray = None

    def _tray_image(self):
        from PIL import Image, ImageDraw

        icon_file = resource_path("assets/worktrack.png")
        if icon_file:
            try:
                return Image.open(icon_file).convert("RGBA")
            except OSError:
                pass

        size = 64
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse([2, 2, 62, 62], fill="#4DABF7")
        draw.ellipse([8, 8, 56, 56], fill="#1A1B1E")
        draw.line([32, 32, 32, 18], fill="white", width=3)
        draw.line([32, 32, 44, 40], fill="white", width=2)
        draw.ellipse([29, 29, 35, 35], fill="#4DABF7")
        return image

    def _tray_open(self):
        if self._root:
            self._root.after(0, self.open_dashboard)

    def _tray_toggle_timer(self):
        if self._root:
            self._root.after(0, self.timer.toggle)

    def _tray_exit(self):
        if self._root:
            self._root.after(0, self.quit)


__all__ = ["AppController", "SingleInstance", "get_data_dir"]
