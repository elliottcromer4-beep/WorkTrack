"""
Compact always-on-top floating widget.
Shows current timer state with start/pause/stop/resume controls.
Draggable by clicking anywhere outside buttons.
"""
from typing import TYPE_CHECKING

import customtkinter as ctk

from ..models import format_duration
from ..timer_engine import TimerStatus
from . import theme as T

if TYPE_CHECKING:
    from .app import AppController


class WidgetWindow(ctk.CTkToplevel):
    WIDTH = 320
    HEIGHT = 130

    @classmethod
    def _is_on_screen(cls, root, x, y) -> bool:
        """True if enough of the widget would land on the virtual desktop to grab."""
        try:
            import ctypes
            gsm = ctypes.windll.user32.GetSystemMetrics
            vx, vy = gsm(76), gsm(77)     # SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN
            vw, vh = gsm(78), gsm(79)     # SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN
        except (AttributeError, OSError):  # non-Windows, or user32 unavailable
            vx, vy = 0, 0
            vw, vh = root.winfo_screenwidth(), root.winfo_screenheight()

        margin = cls.WIDTH // 2
        return ((vx - margin <= x <= vx + vw - margin)
                and (vy <= y <= vy + vh - cls.HEIGHT // 2))

    def __init__(self, root, app: "AppController"):
        super().__init__(root)
        self.app = app
        self._drag_x = 0
        self._drag_y = 0
        self._saved_flash_id = None

        # Position from settings
        x = int(app.db.get_setting("widget_x", "100"))
        y = int(app.db.get_setting("widget_y", "100"))

        # Saved position may belong to a monitor that is no longer attached —
        # fall back to the default corner rather than opening off-screen.
        if not self._is_on_screen(root, x, y):
            x, y = 100, 100

        self.title("WorkTrack")
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.88)   # slightly transparent while working
        self.configure(fg_color=T.BG_RAISED)
        self.overrideredirect(True)   # borderless

        self._build()
        self.refresh_timer_state()
        self._tick()

        # Fully opaque on hover, fade back when mouse leaves
        self.bind("<Enter>", lambda e: self.attributes("-alpha", 1.0))
        self.bind("<Leave>", lambda e: self.attributes("-alpha", 0.88))

        # Drag — bind every non-button surface
        for w in (self._title_bar, self._body, self._ctrl,
                  self._lbl_project, self._lbl_subtask, self._lbl_timer,
                  self._lbl_saved):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_motion)

    # ── Build UI ──────────────────────────────────────────────────────────────

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # ── Title bar ──────────────────────────────────────────────────────
        self._title_bar = ctk.CTkFrame(self, fg_color=T.BG_BASE, corner_radius=0,
                                       height=28)
        self._title_bar.grid(row=0, column=0, sticky="ew")
        self._title_bar.grid_propagate(False)
        self._title_bar.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self._title_bar, text="⏱  WorkTrack",
            font=T.FONT_SMALL, text_color=T.TEXT_MUTED,
            anchor="w"
        ).grid(row=0, column=0, padx=8, pady=4, sticky="w")

        self._lbl_saved = ctk.CTkLabel(
            self._title_bar, text="", font=T.FONT_SMALL,
            text_color=T.SUCCESS, width=50
        )
        self._lbl_saved.grid(row=0, column=1, padx=4)

        btn_kw = {
            "width": 24, "height": 20, "font": T.FONT_SMALL, "corner_radius": 4,
            "fg_color": T.BG_CARD, "hover_color": T.BG_HOVER, "text_color": T.TEXT,
            "border_width": 1, "border_color": T.BORDER,
        }
        ctk.CTkButton(
            self._title_bar, text="▭", **btn_kw, command=self._minimize
        ).grid(row=0, column=2, padx=2, pady=4)
        ctk.CTkButton(
            self._title_bar, text="⊞", **btn_kw, command=self.app.open_dashboard
        ).grid(row=0, column=3, padx=2, pady=4)
        ctk.CTkButton(
            self._title_bar, text="✕", width=24, height=20, font=T.FONT_SMALL,
            corner_radius=4, fg_color=T.BG_CARD, hover_color=T.DANGER,
            text_color=T.TEXT, command=self._quit_app
        ).grid(row=0, column=4, padx=(2, 6), pady=4)

        # ── Body ───────────────────────────────────────────────────────────
        self._body = ctk.CTkFrame(self, fg_color=T.BG_RAISED, corner_radius=0)
        self._body.grid(row=1, column=0, sticky="nsew", padx=0, pady=0)
        self._body.grid_columnconfigure(0, weight=1)
        body = self._body

        # Project / subtask labels
        self._lbl_project = ctk.CTkLabel(
            body, text="No project selected",
            font=T.FONT_MED, text_color=T.TEXT_BRIGHT, anchor="w"
        )
        self._lbl_project.grid(row=0, column=0, padx=12, pady=(6, 0), sticky="ew")

        self._lbl_subtask = ctk.CTkLabel(
            body, text="Click ⊞ to open dashboard",
            font=T.FONT_SMALL, text_color=T.TEXT_MUTED, anchor="w"
        )
        self._lbl_subtask.grid(row=1, column=0, padx=12, sticky="ew")

        # Controls row
        self._ctrl = ctk.CTkFrame(body, fg_color="transparent")
        self._ctrl.grid(row=2, column=0, padx=8, pady=(4, 8), sticky="ew")
        self._ctrl.grid_columnconfigure(0, weight=1)
        ctrl = self._ctrl

        # Timer display
        self._lbl_timer = ctk.CTkLabel(
            ctrl, text="00:00",
            font=T.FONT_TIMER_S, text_color=T.TIMER_STOP, anchor="w"
        )
        self._lbl_timer.grid(row=0, column=0, padx=(4, 8), sticky="w")

        # Buttons
        btn_row = ctk.CTkFrame(ctrl, fg_color="transparent")
        btn_row.grid(row=0, column=1, sticky="e")

        bkw = {"width": 32, "height": 32, "font": ("Segoe UI", 14),
               "corner_radius": 6}

        self._btn_start = ctk.CTkButton(
            btn_row, text="▶", **bkw, fg_color=T.SUCCESS, hover_color="#8CE99A",
            text_color="#1A1B1E", command=self._on_start)
        self._btn_start.grid(row=0, column=0, padx=2)

        self._btn_pause = ctk.CTkButton(
            btn_row, text="⏸", **bkw, fg_color=T.WARNING, hover_color="#FFE066",
            text_color="#1A1B1E", command=self._on_pause)
        self._btn_pause.grid(row=0, column=1, padx=2)

        self._btn_stop = ctk.CTkButton(
            btn_row, text="■", **bkw, fg_color=T.DANGER, hover_color="#FF8787",
            text_color="white", command=self._on_stop)
        self._btn_stop.grid(row=0, column=2, padx=2)

        self._btn_resume = ctk.CTkButton(
            btn_row, text="↺", **bkw, fg_color=T.BG_CARD, hover_color=T.BG_HOVER,
            text_color=T.TEXT, border_width=1, border_color=T.BORDER,
            command=self._on_resume)
        self._btn_resume.grid(row=0, column=3, padx=2)

    # ── Timer tick ────────────────────────────────────────────────────────────

    def _tick(self):
        if not self.winfo_exists():
            return
        try:
            elapsed = self.app.timer.elapsed_seconds
            status = self.app.timer.status

            if status == TimerStatus.RUNNING:
                self._lbl_timer.configure(text=format_duration(elapsed),
                                          text_color=T.TIMER_RUN)
            elif status == TimerStatus.PAUSED:
                self._lbl_timer.configure(text=format_duration(elapsed),
                                          text_color=T.TIMER_PAUSE)
            else:
                self._lbl_timer.configure(text="00:00", text_color=T.TIMER_STOP)
        except Exception:
            pass
        self.after(500, self._tick)

    # ── State sync ────────────────────────────────────────────────────────────

    def refresh_timer_state(self):
        """Called by AppController when timer state changes."""
        try:
            status = self.app.timer.status
            pid = self.app.timer.project_id
            sid = self.app.timer.subtask_id

            if pid:
                proj = self.app.db.get_project(pid)
                pname = proj.name if proj else "Unknown project"
            else:
                pname = "No project selected"

            if sid:
                subtasks = self.app.db.get_subtasks(pid or 0)
                st = next((s for s in subtasks if s.id == sid), None)
                stname = st.name if st else "Unknown task"
            else:
                stname = "Click ⊞ to open dashboard"

            self._lbl_project.configure(text=pname)
            self._lbl_subtask.configure(text=stname)

            # Show/hide buttons based on state
            if status == TimerStatus.STOPPED:
                self._btn_start.configure(state="normal")
                self._btn_pause.configure(state="disabled")
                self._btn_stop.configure(state="disabled")
                self._btn_resume.configure(state="disabled")
            elif status == TimerStatus.RUNNING:
                self._btn_start.configure(state="disabled")
                self._btn_pause.configure(state="normal")
                self._btn_stop.configure(state="normal")
                self._btn_resume.configure(state="disabled")
            elif status == TimerStatus.PAUSED:
                self._btn_start.configure(state="disabled")
                self._btn_pause.configure(state="disabled")
                self._btn_stop.configure(state="normal")
                self._btn_resume.configure(state="normal")
        except Exception:
            pass

    def flash_saved(self):
        self._lbl_saved.configure(text="✓ saved")
        if self._saved_flash_id:
            self.after_cancel(self._saved_flash_id)
        self._saved_flash_id = self.after(
            2000, lambda: self._lbl_saved.configure(text=""))

    # ── Button handlers ───────────────────────────────────────────────────────

    def _on_start(self):
        # Open dashboard so user can pick a project/subtask
        self.app.open_dashboard()

    def _on_pause(self):
        self.app.timer.pause()

    def _on_stop(self):
        self.app.timer.stop()
        if self.app.dashboard:
            self.app.dashboard.refresh_current_panel()

    def _on_resume(self):
        self.app.timer.resume()

    # ── Window controls ───────────────────────────────────────────────────────

    def _minimize(self):
        self.withdraw()

    def _quit_app(self):
        self.app.quit()

    # ── Drag ─────────────────────────────────────────────────────────────────

    def _drag_start(self, event):
        self._drag_x = event.x_root - self.winfo_x()
        self._drag_y = event.y_root - self.winfo_y()

    def _drag_motion(self, event):
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self.geometry(f"+{x}+{y}")
