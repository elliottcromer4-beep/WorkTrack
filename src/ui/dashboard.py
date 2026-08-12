"""
The dashboard window: a sidebar of panels over a shared content area.

Panels: Projects | History | Reports | Exports | Settings.
"""
from datetime import timedelta
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import TYPE_CHECKING, Optional

import customtkinter as ctk

from ..exports.common import default_filename, period_label
from ..models import (
    Project,
    Session,
    Subtask,
    format_duration,
    format_duration_human,
    format_hm,
    format_hours_decimal,
)
from ..system import open_path
from ..timeutil import (
    fmt_datetime,
    iso_utc,
    local_day_end,
    local_day_start,
    now_local,
    parse_date_input,
    parse_datetime_input,
    parse_local,
)
from ..version import APP_NAME, APP_TAGLINE, __version__
from . import theme as T

if TYPE_CHECKING:
    from .app import AppController


def _confirm(parent, title: str, message: str) -> bool:
    """Ask before doing something the user cannot simply retype."""
    return messagebox.askyesno(title, message, parent=parent, icon="warning")


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard window
# ─────────────────────────────────────────────────────────────────────────────

class DashboardWindow(ctk.CTkToplevel):
    NAV_ITEMS = [
        ("Projects", "Projects and tasks"),
        ("History", "Recorded sessions"),
        ("Reports", "Time by project"),
        ("Exports", "PDF, Excel and CSV"),
        ("Settings", "Preferences and data"),
    ]

    def __init__(self, root, app: "AppController"):
        super().__init__(root)
        self.app = app
        self._current_panel = None
        self._panels: dict[str, ctk.CTkFrame] = {}
        self._active_nav: Optional[str] = None
        self._search_var = ctk.StringVar()
        self._search_debounce_id = None
        self._toast_id = None

        self.title(f"{APP_NAME} — {APP_TAGLINE}")
        self.geometry(app.db.get_setting("dashboard_geometry", "1000x700+200+100"))
        self.minsize(880, 600)
        self.configure(fg_color=T.BG_BASE)
        self.protocol("WM_DELETE_WINDOW", self.withdraw)

        self._build()
        self._switch_panel("Projects")

        self.bind("<Control-s>", lambda _e: self.app._manual_save())
        self.bind("<Control-z>", lambda _e: self._undo())
        self.bind("<Control-f>", lambda _e: self._focus_search())
        self.withdraw()  # the floating widget decides when this is shown

    # ── Layout ────────────────────────────────────────────────────────────

    def _build(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        sidebar = ctk.CTkFrame(self, fg_color=T.BG_BASE, width=196, corner_radius=0)
        sidebar.grid(row=0, column=0, rowspan=2, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(10, weight=1)

        ctk.CTkLabel(sidebar, text=APP_NAME, font=T.FONT_TITLE,
                     text_color=T.ACCENT).grid(row=0, column=0, padx=18,
                                               pady=(22, 0), sticky="w")
        ctk.CTkLabel(sidebar, text=APP_TAGLINE, font=T.FONT_TINY,
                     text_color=T.TEXT_MUTED).grid(row=1, column=0, padx=18,
                                                   pady=(0, 16), sticky="w")

        self._search = ctk.CTkEntry(
            sidebar, placeholder_text="Search projects…",
            textvariable=self._search_var,
            fg_color=T.BG_CARD, border_color=T.BORDER, text_color=T.TEXT,
            placeholder_text_color=T.TEXT_MUTED, font=T.FONT_SMALL, height=32,
        )
        self._search.grid(row=2, column=0, padx=12, pady=(0, 14), sticky="ew")
        self._search_var.trace_add("write", lambda *_: self._search_debounced())

        self._nav_buttons = {}
        for index, (label, _hint) in enumerate(self.NAV_ITEMS):
            button = ctk.CTkButton(
                sidebar, text=f"   {label}", font=T.FONT_BASE, anchor="w",
                height=40, corner_radius=T.RADIUS_MD, fg_color="transparent",
                hover_color=T.BG_HOVER, text_color=T.TEXT_MUTED,
                command=lambda name=label: self._switch_panel(name),
            )
            button.grid(row=3 + index, column=0, padx=10, pady=2, sticky="ew")
            self._nav_buttons[label] = button

        # Timer status, always visible regardless of the open panel.
        strip = ctk.CTkFrame(sidebar, fg_color=T.BG_CARD,
                             corner_radius=T.RADIUS_MD, height=78)
        strip.grid(row=11, column=0, padx=10, pady=(12, 8), sticky="ew")
        strip.grid_propagate(False)
        strip.grid_columnconfigure(0, weight=1)

        self._sb_status = ctk.CTkLabel(strip, text="No timer running",
                                       font=T.FONT_SMALL, text_color=T.TEXT_MUTED,
                                       wraplength=150, justify="left", anchor="w")
        self._sb_status.grid(row=0, column=0, padx=10, pady=(8, 0), sticky="ew")
        self._sb_time = ctk.CTkLabel(strip, text="—", font=T.FONT_TIMER_S,
                                     text_color=T.TIMER_STOP, anchor="w")
        self._sb_time.grid(row=1, column=0, padx=10, pady=(0, 8), sticky="ew")

        ctk.CTkLabel(sidebar, text=f"v{__version__}", font=T.FONT_TINY,
                     text_color=T.TEXT_DIM).grid(row=12, column=0, padx=18,
                                                 pady=(0, 12), sticky="w")

        self._content = ctk.CTkFrame(self, fg_color=T.BG_RAISED, corner_radius=0)
        self._content.grid(row=0, column=1, sticky="nsew")
        self._content.grid_columnconfigure(0, weight=1)
        self._content.grid_rowconfigure(0, weight=1)

        self._toast = ctk.CTkLabel(self, text="", font=T.FONT_SMALL,
                                   text_color=T.TEXT_MUTED, anchor="w", height=0)
        self._toast.grid(row=1, column=1, sticky="ew", padx=20, pady=(0, 6))

        self._tick_sidebar()

    def _tick_sidebar(self):
        if not self.winfo_exists():
            return
        try:
            timer = self.app.timer
            if timer.is_active:
                self._sb_time.configure(
                    text=format_duration(timer.elapsed_seconds),
                    text_color=T.TIMER_RUN if timer.is_running else T.TIMER_PAUSE,
                )
                project = (self.app.db.get_project(timer.project_id)
                           if timer.project_id else None)
                marker = "Running" if timer.is_running else "Paused"
                name = project.name if project else "—"
                self._sb_status.configure(text=f"{marker} · {name}", text_color=T.TEXT)
            else:
                self._sb_time.configure(text="—", text_color=T.TIMER_STOP)
                self._sb_status.configure(text="No timer running",
                                          text_color=T.TEXT_MUTED)
        except Exception:
            pass
        self.after(500, self._tick_sidebar)

    # ── Toast ─────────────────────────────────────────────────────────────

    def notify(self, message: str, tone: str = "info", seconds: int = 6):
        colour = {"info": T.TEXT_MUTED, "ok": T.SUCCESS, "error": T.DANGER}.get(
            tone, T.TEXT_MUTED)
        self._toast.configure(text=message, text_color=colour)
        if self._toast_id:
            self.after_cancel(self._toast_id)
        self._toast_id = self.after(seconds * 1000,
                                    lambda: self._toast.configure(text=""))

    # ── Panels ────────────────────────────────────────────────────────────

    def _switch_panel(self, name: str):
        if self._active_nav in self._nav_buttons:
            self._nav_buttons[self._active_nav].configure(
                fg_color="transparent", text_color=T.TEXT_MUTED)
        if name in self._nav_buttons:
            self._nav_buttons[name].configure(fg_color=T.ACCENT_BG,
                                              text_color=T.ACCENT)
        self._active_nav = name

        if self._current_panel:
            self._current_panel.grid_remove()

        if name not in self._panels:
            panel_class = {
                "Projects": ProjectsPanel,
                "History": HistoryPanel,
                "Reports": ReportsPanel,
                "Exports": ExportsPanel,
                "Settings": SettingsPanel,
            }[name]
            panel = panel_class(self._content, self.app)
            panel.grid(row=0, column=0, sticky="nsew")
            self._panels[name] = panel
        else:
            self._panels[name].grid()
            self._panels[name].on_show()

        self._current_panel = self._panels[name]

    def _focus_search(self):
        self.deiconify()
        self.lift()
        self._search.focus_set()
        self._search.select_range(0, "end")

    def _search_debounced(self):
        if self._search_debounce_id:
            self.after_cancel(self._search_debounce_id)
        self._search_debounce_id = self.after(250, self._do_search)

    def _do_search(self):
        query = self._search_var.get().strip()
        if "Projects" not in self._panels:
            self._switch_panel("Projects")
        self._panels["Projects"].apply_search(query)
        if query:
            self._switch_panel("Projects")

    def _undo(self):
        description = self.app.undo()
        if description:
            self.notify(description, "ok")
            self.refresh_current_panel()
        else:
            self.notify("Nothing to undo")

    # ── Called by AppController ───────────────────────────────────────────

    def on_timer_change(self):
        if "Projects" in self._panels:
            self._panels["Projects"].refresh_timer_buttons()

    def refresh_current_panel(self):
        if self._current_panel:
            self._current_panel.on_show()

    def rebuild_panels(self, keep: str):
        """
        Discard cached panels so they are rebuilt against fresh data.

        Used after a restore, when the rows a panel was built from no longer
        exist.  The panels must be destroyed, not just forgotten, or their
        widgets stay on screen showing the old data.
        """
        for name in list(self._panels):
            if name == keep:
                continue
            self._panels.pop(name).destroy()


# ─────────────────────────────────────────────────────────────────────────────
# Shared panel furniture
# ─────────────────────────────────────────────────────────────────────────────

class Panel(ctk.CTkFrame):
    """Base panel: a title row over a content area."""

    title = ""
    subtitle = ""

    def __init__(self, parent, app: "AppController"):
        super().__init__(parent, fg_color=T.BG_RAISED, corner_radius=0)
        self.app = app
        self.grid_columnconfigure(0, weight=1)

    @property
    def dashboard(self) -> Optional[DashboardWindow]:
        widget = self.master
        while widget is not None and not isinstance(widget, DashboardWindow):
            widget = getattr(widget, "master", None)
        return widget

    def notify(self, message: str, tone: str = "info"):
        board = self.dashboard
        if board:
            board.notify(message, tone)

    def header(self, row: int, title: str, subtitle: str = "") -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid(row=row, column=0, sticky="ew", padx=24, pady=(20, 10))
        frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(frame, text=title, font=T.FONT_HEADING,
                     text_color=T.TEXT_BRIGHT, anchor="w").grid(
            row=0, column=0, sticky="w")
        if subtitle:
            ctk.CTkLabel(frame, text=subtitle, font=T.FONT_SMALL,
                         text_color=T.TEXT_MUTED, anchor="w").grid(
                row=1, column=0, sticky="w", pady=(2, 0))
        return frame

    def on_show(self):
        pass


def segmented(parent, options: list[str], active: str,
              command) -> dict[str, ctk.CTkButton]:
    """A row of mutually exclusive filter buttons."""
    buttons: dict[str, ctk.CTkButton] = {}
    for index, label in enumerate(options):
        is_active = label == active
        button = ctk.CTkButton(
            parent, text=label, width=78, height=30, font=T.FONT_SMALL,
            corner_radius=T.RADIUS_SM,
            fg_color=T.ACCENT if is_active else T.BG_CARD,
            hover_color=T.ACCENT_HOVER if is_active else T.BG_HOVER,
            text_color="white" if is_active else T.TEXT,
            command=lambda name=label: command(name),
        )
        button.grid(row=0, column=index, padx=(0, 6))
        buttons[label] = button
    return buttons


def set_segmented(buttons: dict[str, ctk.CTkButton], active: str):
    for label, button in buttons.items():
        is_active = label == active
        button.configure(
            fg_color=T.ACCENT if is_active else T.BG_CARD,
            hover_color=T.ACCENT_HOVER if is_active else T.BG_HOVER,
            text_color="white" if is_active else T.TEXT,
        )


def period_bounds(period: str):
    """Local-calendar bounds for a named period, as stored UTC strings."""
    today = now_local()
    if period == "Today":
        start = local_day_start(today)
    elif period == "Week":
        start = local_day_start(today - timedelta(days=today.weekday()))
    elif period == "Month":
        start = local_day_start(today.replace(day=1))
    else:
        return None, None
    return start.isoformat(), local_day_end(today).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Projects
# ─────────────────────────────────────────────────────────────────────────────

class ProjectsPanel(Panel):
    def __init__(self, parent, app: "AppController"):
        super().__init__(parent, app)
        self._selected_project_id: Optional[int] = None
        self._search_filter = ""
        self._new_subtask_var = ctk.StringVar()
        self.grid_rowconfigure(0, weight=1)
        self._build()
        self.on_show()

    def _build(self):
        split = ctk.CTkFrame(self, fg_color="transparent")
        split.grid(row=0, column=0, sticky="nsew")
        split.grid_columnconfigure(0, weight=2, uniform="cols")
        split.grid_columnconfigure(1, weight=3, uniform="cols")
        split.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(split, fg_color=T.BG_BASE, corner_radius=0)
        left.grid(row=0, column=0, sticky="nsew")
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(left, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(18, 8))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="Projects", font=T.FONT_TITLE,
                     text_color=T.TEXT_BRIGHT).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(head, text="New project", font=T.FONT_SMALL, width=100,
                      height=30, corner_radius=T.RADIUS_SM, **T.BTN_PRIMARY,
                      command=self._new_project).grid(row=0, column=1)

        self._project_list = ctk.CTkScrollableFrame(
            left, fg_color="transparent", scrollbar_button_color=T.BG_ACTIVE)
        self._project_list.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self._project_list.grid_columnconfigure(0, weight=1)

        self._detail = ctk.CTkFrame(split, fg_color=T.BG_RAISED, corner_radius=0)
        self._detail.grid(row=0, column=1, sticky="nsew")
        self._detail.grid_columnconfigure(0, weight=1)
        self._show_placeholder()

    def _show_placeholder(self):
        for child in self._detail.winfo_children():
            child.destroy()
        holder = ctk.CTkFrame(self._detail, fg_color="transparent")
        holder.place(relx=0.5, rely=0.45, anchor="center")
        ctk.CTkLabel(holder, text="Select a project", font=T.FONT_LARGE,
                     text_color=T.TEXT_MUTED).pack()
        ctk.CTkLabel(holder, text="Its tasks and timers appear here.",
                     font=T.FONT_SMALL, text_color=T.TEXT_DIM).pack(pady=(4, 0))

    def on_show(self):
        self._refresh_project_list()
        if self._selected_project_id:
            self._show_project_detail(self._selected_project_id)

    def apply_search(self, query: str):
        self._search_filter = query
        self._refresh_project_list()

    def _refresh_project_list(self):
        for child in self._project_list.winfo_children():
            child.destroy()

        projects = self.app.db.get_projects()
        if self._search_filter:
            needle = self._search_filter.lower()
            projects = [p for p in projects
                        if needle in p.name.lower() or needle in p.client.lower()]

        if not projects:
            message = ("No projects match your search."
                       if self._search_filter
                       else "No projects yet.\nUse “New project” to create one.")
            ctk.CTkLabel(self._project_list, text=message, font=T.FONT_BASE,
                         text_color=T.TEXT_MUTED, justify="center").grid(
                row=0, column=0, pady=48)
            return

        totals = self.app.db.get_project_totals()
        for index, project in enumerate(projects):
            self._add_project_row(index, project, totals.get(project.id, 0.0))

    def _add_project_row(self, row: int, project: Project, total: float):
        timer = self.app.timer
        is_selected = project.id == self._selected_project_id
        is_timing = timer.is_active and timer.project_id == project.id

        frame = ctk.CTkFrame(
            self._project_list,
            fg_color=T.BG_ACTIVE if is_selected else T.BG_CARD,
            corner_radius=T.RADIUS_MD, height=68,
            border_width=1,
            border_color=T.ACCENT if is_selected else T.BG_CARD,
        )
        frame.grid(row=row, column=0, sticky="ew", padx=4, pady=3)
        frame.grid_columnconfigure(1, weight=1)
        frame.grid_propagate(False)

        dot = ctk.CTkLabel(frame, text="●", font=T.FONT_LARGE,
                           text_color=project.color or T.ACCENT, width=18)
        dot.grid(row=0, column=0, rowspan=2, padx=(12, 6), sticky="w")

        name = ctk.CTkLabel(frame, text=project.name, font=T.FONT_MED, anchor="w",
                            text_color=T.TEXT_BRIGHT if is_selected else T.TEXT)
        name.grid(row=0, column=1, sticky="w", pady=(12, 0))
        client = ctk.CTkLabel(frame, text=project.client or "No client",
                              font=T.FONT_SMALL, text_color=T.TEXT_MUTED, anchor="w")
        client.grid(row=1, column=1, sticky="w", pady=(0, 12))

        total_label = ctk.CTkLabel(
            frame, text=format_duration_human(total), font=T.FONT_MONO, anchor="e",
            text_color=T.TIMER_RUN if is_timing else T.TEXT_MUTED)
        total_label.grid(row=0, column=2, rowspan=2, padx=14, sticky="e")

        for widget in (frame, dot, name, client, total_label):
            widget.bind("<Button-1>",
                        lambda _e, pid=project.id: self._select_project(pid))
            widget.bind("<Double-Button-1>",
                        lambda _e, pid=project.id: self._edit_project(pid))

    def _select_project(self, project_id: int):
        self._selected_project_id = project_id
        self._refresh_project_list()
        self._show_project_detail(project_id)

    def _show_project_detail(self, project_id: int):
        for child in self._detail.winfo_children():
            child.destroy()

        project = self.app.db.get_project(project_id)
        if not project:
            self._selected_project_id = None
            self._show_placeholder()
            return

        self._detail.grid_rowconfigure(2, weight=1)

        head = ctk.CTkFrame(self._detail, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=22, pady=(20, 0))
        head.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(head, text=project.name, font=T.FONT_TITLE,
                     text_color=T.TEXT_BRIGHT, anchor="w").grid(
            row=0, column=0, sticky="w")
        if project.client:
            ctk.CTkLabel(head, text=project.client, font=T.FONT_SMALL,
                         text_color=T.TEXT_MUTED, anchor="w").grid(
                row=1, column=0, sticky="w", pady=(2, 0))

        actions = ctk.CTkFrame(head, fg_color="transparent")
        actions.grid(row=0, column=1, rowspan=2, sticky="e")
        button_kw = {"width": 76, "height": 30, "font": T.FONT_SMALL,
                         "corner_radius": T.RADIUS_SM}
        ctk.CTkButton(actions, text="Edit", **button_kw, **T.BTN_GHOST,
                      command=lambda: self._edit_project(project_id)).grid(
            row=0, column=0, padx=3)
        ctk.CTkButton(actions, text="Duplicate", **button_kw, **T.BTN_GHOST,
                      command=lambda: self._duplicate_project(project_id)).grid(
            row=0, column=1, padx=3)
        ctk.CTkButton(actions, text="Archive", **button_kw, **T.BTN_GHOST,
                      command=lambda: self._archive_project(project_id)).grid(
            row=0, column=2, padx=3)

        if project.notes:
            ctk.CTkLabel(self._detail, text=project.notes, font=T.FONT_SMALL,
                         text_color=T.TEXT_MUTED, anchor="w", justify="left",
                         wraplength=460).grid(row=1, column=0, sticky="w",
                                              padx=22, pady=(10, 0))

        body = ctk.CTkFrame(self._detail, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=22, pady=(14, 18))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)

        task_head = ctk.CTkFrame(body, fg_color="transparent")
        task_head.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        task_head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(task_head, text="Tasks", font=T.FONT_LARGE,
                     text_color=T.TEXT_BRIGHT).grid(row=0, column=0, sticky="w")

        adder = ctk.CTkFrame(task_head, fg_color="transparent")
        adder.grid(row=0, column=1, sticky="e")
        self._new_subtask_var.set("")
        entry = ctk.CTkEntry(adder, textvariable=self._new_subtask_var,
                             placeholder_text="New task name…",
                             fg_color=T.BG_CARD, border_color=T.BORDER,
                             text_color=T.TEXT, font=T.FONT_BASE,
                             width=190, height=30)
        entry.grid(row=0, column=0, padx=(0, 6))
        entry.bind("<Return>", lambda _e: self._add_subtask(project_id))
        ctk.CTkButton(adder, text="Add", width=56, height=30, font=T.FONT_SMALL,
                      corner_radius=T.RADIUS_SM, **T.BTN_PRIMARY,
                      command=lambda: self._add_subtask(project_id)).grid(
            row=0, column=1)

        task_list = ctk.CTkScrollableFrame(body, fg_color="transparent")
        task_list.grid(row=1, column=0, sticky="nsew")
        task_list.grid_columnconfigure(0, weight=1)

        subtasks = self.app.db.get_subtasks(project_id)
        if not subtasks:
            ctk.CTkLabel(task_list,
                         text="No tasks yet.\nType a name above and press Enter.",
                         font=T.FONT_BASE, text_color=T.TEXT_MUTED,
                         justify="center").grid(row=0, column=0, pady=36)
            return

        totals = self.app.db.get_subtask_totals(project_id)
        for index, subtask in enumerate(subtasks):
            self._add_subtask_row(task_list, index, subtask, project_id,
                                  totals.get(subtask.id, 0.0))

    def _add_subtask_row(self, parent, row: int, subtask: Subtask,
                         project_id: int, total: float):
        timer = self.app.timer
        is_timing = timer.is_active and timer.subtask_id == subtask.id
        if is_timing:
            total += timer.elapsed_seconds

        frame = ctk.CTkFrame(parent, fg_color=T.SUCCESS_BG if is_timing else T.BG_CARD,
                             corner_radius=T.RADIUS_SM, height=50)
        frame.grid(row=row, column=0, sticky="ew", pady=2)
        frame.grid_columnconfigure(1, weight=1)
        frame.grid_propagate(False)

        ctk.CTkLabel(frame, text="▶" if is_timing else "·", width=16,
                     font=T.FONT_BASE,
                     text_color=T.SUCCESS if is_timing else T.TEXT_DIM).grid(
            row=0, column=0, padx=(10, 4))

        name = ctk.CTkLabel(frame, text=subtask.name, font=T.FONT_BASE, anchor="w",
                            text_color=T.TEXT_BRIGHT if is_timing else T.TEXT)
        name.grid(row=0, column=1, sticky="ew", pady=13)
        name.bind("<Double-Button-1>",
                  lambda _e: self._rename_subtask(subtask.id, name, subtask.name))

        ctk.CTkLabel(frame, text=format_duration_human(total) if total else "—",
                     font=T.FONT_MONO, width=68, anchor="e",
                     text_color=T.TIMER_RUN if is_timing else T.TEXT_MUTED).grid(
            row=0, column=2, padx=8)

        buttons = ctk.CTkFrame(frame, fg_color="transparent")
        buttons.grid(row=0, column=3, padx=(0, 8))
        button_kw = {"width": 32, "height": 32, "font": ("Segoe UI", 13),
                         "corner_radius": T.RADIUS_SM}

        if is_timing:
            if timer.is_running:
                ctk.CTkButton(buttons, text="⏸", **button_kw, fg_color=T.WARNING,
                              hover_color="#FFE066", text_color="#1A1B1E",
                              command=timer.pause).grid(row=0, column=0, padx=2)
            else:
                ctk.CTkButton(buttons, text="▶", **button_kw, fg_color=T.SUCCESS,
                              hover_color="#8CE99A", text_color="#1A1B1E",
                              command=timer.resume).grid(row=0, column=0, padx=2)
            ctk.CTkButton(buttons, text="■", **button_kw, fg_color=T.DANGER,
                          hover_color="#FF8787", text_color="white",
                          command=self._stop_timer).grid(row=0, column=1, padx=2)
        else:
            ctk.CTkButton(buttons, text="▶", **button_kw, fg_color=T.SUCCESS,
                          hover_color="#8CE99A", text_color="#1A1B1E",
                          command=lambda: self._start_timer(project_id, subtask.id)
                          ).grid(row=0, column=0, padx=2)
            ctk.CTkButton(buttons, text="🗑", **button_kw, **T.BTN_GHOST,
                          command=lambda: self._delete_subtask(
                              subtask.id, subtask.name, project_id)).grid(
                row=0, column=1, padx=2)

    # ── Actions ───────────────────────────────────────────────────────────

    def refresh_timer_buttons(self):
        if self._selected_project_id:
            self._show_project_detail(self._selected_project_id)
        self._refresh_project_list()

    def _start_timer(self, project_id: int, subtask_id: int):
        self.app.timer.start(project_id, subtask_id)
        self.refresh_timer_buttons()

    def _stop_timer(self):
        self.app.timer.stop()
        self.refresh_timer_buttons()

    def _add_subtask(self, project_id: int):
        name = self._new_subtask_var.get().strip()
        if not name:
            return
        position = len(self.app.db.get_subtasks(project_id))
        self.app.db.create_subtask(project_id, name, position)
        self._new_subtask_var.set("")
        self._show_project_detail(project_id)

    def _rename_subtask(self, subtask_id: int, label: ctk.CTkLabel, current: str):
        variable = ctk.StringVar(value=current)
        entry = ctk.CTkEntry(label.master, textvariable=variable, font=T.FONT_BASE,
                             fg_color=T.BG_HOVER, border_color=T.ACCENT,
                             text_color=T.TEXT_BRIGHT, height=28)
        entry.place(in_=label, relwidth=1.0, x=0, y=0, anchor="nw")
        entry.focus_set()
        entry.select_range(0, "end")
        saved = {"done": False}

        def save(_event=None):
            if saved["done"]:
                return
            saved["done"] = True
            name = variable.get().strip()
            entry.destroy()
            if name and name != current:
                self.app.db.update_subtask(subtask_id, name)
                label.configure(text=name)

        def cancel(_event=None):
            saved["done"] = True
            entry.destroy()

        entry.bind("<Return>", save)
        entry.bind("<Escape>", cancel)
        entry.bind("<FocusOut>", save)

    def _delete_subtask(self, subtask_id: int, name: str, project_id: int):
        recorded = self.app.db.get_subtask_total_seconds(subtask_id)
        detail = (f"\n\n{format_duration_human(recorded)} is recorded against it. "
                  "The sessions are kept and can be restored with Ctrl+Z."
                  if recorded else "")
        if not _confirm(self, "Delete task", f"Delete “{name}”?{detail}"):
            return
        self.app.db.delete_subtask(subtask_id)
        self.app.push_undo(lambda: self.app.db.restore_subtask(subtask_id),
                           f"Restored task “{name}”")
        self._show_project_detail(project_id)
        self.notify(f"Deleted “{name}” — Ctrl+Z to undo", "ok")

    def _new_project(self):
        ProjectDialog(self, self.app, on_save=self.on_show)

    def _edit_project(self, project_id: int):
        project = self.app.db.get_project(project_id)
        if project:
            ProjectDialog(self, self.app, project=project, on_save=self.on_show)

    def _duplicate_project(self, project_id: int):
        new_id = self.app.db.duplicate_project(project_id)
        if new_id > 0:
            self._selected_project_id = new_id
            self.on_show()
            self.notify("Project duplicated", "ok")

    def _archive_project(self, project_id: int):
        project = self.app.db.get_project(project_id)
        if not project:
            return
        if not _confirm(self, "Archive project",
                        f"Archive “{project.name}”?\n\n"
                        "It is hidden from the project list but its recorded time "
                        "is kept and still appears in reports."):
            return
        self.app.db.archive_project(project_id, True)
        self.app.push_undo(lambda: self.app.db.archive_project(project_id, False),
                           f"Restored “{project.name}”")
        self._selected_project_id = None
        self._show_placeholder()
        self.on_show()
        self.notify(f"Archived “{project.name}” — Ctrl+Z to undo", "ok")


# ─────────────────────────────────────────────────────────────────────────────
# Project dialog
# ─────────────────────────────────────────────────────────────────────────────

class ProjectDialog(ctk.CTkToplevel):
    def __init__(self, parent, app: "AppController",
                 project: Optional[Project] = None, on_save=None):
        super().__init__(parent)
        self.app = app
        self.project = project
        self.on_save = on_save
        self._color = project.color if project else T.PROJECT_COLORS[0]
        self._swatches: dict[str, ctk.CTkButton] = {}

        self.title("Edit project" if project else "New project")
        self.geometry("440x470")
        self.resizable(False, False)
        self.configure(fg_color=T.BG_RAISED)
        self.transient(parent)
        self.grab_set()

        self._build()
        self.bind("<Return>", lambda _e: self._save())
        self.bind("<Escape>", lambda _e: self.destroy())

    def _build(self):
        pad = {"padx": 24, "pady": (10, 0)}

        def label(text):
            ctk.CTkLabel(self, text=text, font=T.FONT_LABEL,
                         text_color=T.TEXT_MUTED, anchor="w").pack(fill="x", **pad)

        label("Project name")
        self._name = ctk.CTkEntry(self, fg_color=T.BG_CARD, border_color=T.BORDER,
                                  text_color=T.TEXT_BRIGHT, font=T.FONT_MED,
                                  height=38)
        self._name.pack(fill="x", padx=24, pady=(4, 0))

        label("Client or company")
        self._client = ctk.CTkEntry(self, fg_color=T.BG_CARD, border_color=T.BORDER,
                                    text_color=T.TEXT_BRIGHT, font=T.FONT_MED,
                                    height=38)
        self._client.pack(fill="x", padx=24, pady=(4, 0))

        label("Notes")
        self._notes = ctk.CTkTextbox(self, fg_color=T.BG_CARD, border_color=T.BORDER,
                                     text_color=T.TEXT, font=T.FONT_BASE, height=72,
                                     border_width=1)
        self._notes.pack(fill="x", padx=24, pady=(4, 0))

        label("Colour")
        swatch_row = ctk.CTkFrame(self, fg_color="transparent")
        swatch_row.pack(fill="x", padx=24, pady=(6, 0))
        for colour in T.PROJECT_COLORS:
            button = ctk.CTkButton(swatch_row, text="", width=26, height=26,
                                   corner_radius=13, fg_color=colour,
                                   hover_color=colour, border_width=2,
                                   border_color=T.BG_RAISED,
                                   command=lambda c=colour: self._pick_colour(c))
            button.pack(side="left", padx=3)
            self._swatches[colour] = button
        self._pick_colour(self._color)

        self._error = ctk.CTkLabel(self, text="", font=T.FONT_SMALL,
                                   text_color=T.DANGER, anchor="w")
        self._error.pack(fill="x", padx=24, pady=(8, 0))

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(fill="x", padx=24, pady=(4, 18), side="bottom")
        ctk.CTkButton(buttons, text="Cancel", width=100, height=36,
                      corner_radius=T.RADIUS_SM, **T.BTN_GHOST,
                      command=self.destroy).pack(side="left")
        ctk.CTkButton(buttons, text="Save", width=100, height=36,
                      corner_radius=T.RADIUS_SM, **T.BTN_PRIMARY,
                      command=self._save).pack(side="right")

        if self.project:
            self._name.insert(0, self.project.name)
            self._client.insert(0, self.project.client)
            if self.project.notes:
                self._notes.insert("1.0", self.project.notes)
        self._name.focus_set()

    def _pick_colour(self, colour: str):
        self._color = colour
        for value, button in self._swatches.items():
            button.configure(border_color=T.TEXT_BRIGHT if value == colour
                             else T.BG_RAISED)

    def _save(self):
        name = self._name.get().strip()
        if not name:
            self._name.configure(border_color=T.DANGER)
            self._error.configure(text="A project needs a name.")
            return
        client = self._client.get().strip()
        notes = self._notes.get("1.0", "end-1c").strip()
        if self.project:
            self.app.db.update_project(self.project.id, name, client, notes,
                                       self._color)
        else:
            self.app.db.create_project(name, client, notes, self._color)
        self.destroy()
        if self.on_save:
            self.on_save()


# ─────────────────────────────────────────────────────────────────────────────
# History
# ─────────────────────────────────────────────────────────────────────────────

class HistoryPanel(Panel):
    PERIODS = ["Today", "Week", "Month", "All"]
    PAGE_SIZE = 200

    def __init__(self, parent, app: "AppController"):
        super().__init__(parent, app)
        self._period = "Today"
        self.grid_rowconfigure(1, weight=1)
        self._build()
        self.on_show()

    def _build(self):
        head = self.header(0, "History", "Every recorded session, newest first")

        controls = ctk.CTkFrame(head, fg_color="transparent")
        controls.grid(row=0, column=1, rowspan=2, sticky="e")
        filters = ctk.CTkFrame(controls, fg_color="transparent")
        filters.grid(row=0, column=0, padx=(0, 8))
        self._period_buttons = segmented(filters, self.PERIODS, self._period,
                                         self._set_period)
        ctk.CTkButton(controls, text="Add entry", width=96, height=30,
                      font=T.FONT_SMALL, corner_radius=T.RADIUS_SM, **T.BTN_PRIMARY,
                      command=self._add_entry).grid(row=0, column=1)

        self._list = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._list.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 6))
        self._list.grid_columnconfigure(0, weight=1)

        footer = ctk.CTkFrame(self, fg_color=T.BG_CARD, height=42,
                              corner_radius=T.RADIUS_MD)
        footer.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 16))
        footer.grid_propagate(False)
        footer.grid_columnconfigure(0, weight=1)
        self._summary = ctk.CTkLabel(footer, text="", font=T.FONT_SMALL,
                                     text_color=T.TEXT_MUTED, anchor="w")
        self._summary.grid(row=0, column=0, padx=16, sticky="w")
        self._total = ctk.CTkLabel(footer, text="", font=T.FONT_MONO_L,
                                   text_color=T.TEXT_BRIGHT, anchor="e")
        self._total.grid(row=0, column=1, padx=16, sticky="e")

    def _set_period(self, period: str):
        self._period = period
        set_segmented(self._period_buttons, period)
        self._load()

    def on_show(self):
        self._load()

    def _load(self):
        for child in self._list.winfo_children():
            child.destroy()

        date_from, date_to = period_bounds(self._period)
        sessions = self.app.db.get_sessions(date_from=date_from, date_to=date_to,
                                            limit=self.PAGE_SIZE + 1)
        truncated = len(sessions) > self.PAGE_SIZE
        sessions = sessions[:self.PAGE_SIZE]

        if not sessions:
            ctk.CTkLabel(self._list, text="No sessions in this period.",
                         font=T.FONT_BASE, text_color=T.TEXT_MUTED).grid(
                row=0, column=0, pady=40)
            self._summary.configure(text=period_label(date_from, date_to))
            self._total.configure(text="—")
            return

        total = 0.0
        for index, session in enumerate(sessions):
            self._add_row(index, session)
            total += session.duration_seconds

        note = f"{len(sessions)} sessions"
        if truncated:
            note += f" (showing the most recent {self.PAGE_SIZE})"
        self._summary.configure(text=f"{period_label(date_from, date_to)} · {note}")
        self._total.configure(
            text=f"{format_hm(total)}   ·   {format_hours_decimal(total)} h")

    def _add_row(self, row: int, session: Session):
        frame = ctk.CTkFrame(self._list, fg_color=T.BG_CARD,
                             corner_radius=T.RADIUS_SM, height=54)
        frame.grid(row=row, column=0, sticky="ew", pady=2)
        frame.grid_columnconfigure(1, weight=1)
        frame.grid_propagate(False)

        ctk.CTkLabel(frame, text=fmt_datetime(session.started_at, "%d %b  %H:%M"),
                     font=T.FONT_MONO, text_color=T.TEXT_MUTED, width=124,
                     anchor="w").grid(row=0, column=0, padx=(14, 8), pady=15)

        ctk.CTkLabel(frame,
                     text=f"{session.project_name}  ›  {session.subtask_name}",
                     font=T.FONT_BASE, text_color=T.TEXT, anchor="w").grid(
            row=0, column=1, sticky="ew", pady=15)

        ctk.CTkLabel(frame, text=format_hm(session.duration_seconds),
                     font=T.FONT_MONO, text_color=T.ACCENT, width=64,
                     anchor="e").grid(row=0, column=2, padx=8)

        actions = ctk.CTkFrame(frame, fg_color="transparent")
        actions.grid(row=0, column=3, padx=(0, 10))
        button_kw = {"width": 30, "height": 30, "font": T.FONT_SMALL,
                         "corner_radius": T.RADIUS_SM}
        ctk.CTkButton(actions, text="✎", **button_kw, **T.BTN_GHOST,
                      command=lambda: self._edit(session.id)).grid(row=0, column=0,
                                                                   padx=2)
        ctk.CTkButton(actions, text="🗑", **button_kw, **T.BTN_GHOST,
                      command=lambda: self._delete(session.id)).grid(row=0, column=1,
                                                                     padx=2)

    def _add_entry(self):
        projects = self.app.db.get_projects()
        if not projects:
            self.notify("Create a project first.", "error")
            return
        SessionDialog(self, self.app, session=None, on_save=self._load)

    def _edit(self, session_id: int):
        session = self.app.db.get_session(session_id)
        if session:
            SessionDialog(self, self.app, session=session, on_save=self._load)

    def _delete(self, session_id: int):
        session = self.app.db.get_session(session_id)
        if not session:
            return
        if not _confirm(self, "Delete session",
                        f"Delete this {format_duration_human(session.duration_seconds)} "
                        f"session on “{session.subtask_name}”?"):
            return
        self.app.db.delete_session(session_id)
        self.app.push_undo(lambda: self.app.db.restore_session(session),
                           "Restored session")
        self._load()
        self.notify("Session deleted — Ctrl+Z to undo", "ok")


# ─────────────────────────────────────────────────────────────────────────────
# Session dialog
# ─────────────────────────────────────────────────────────────────────────────

class SessionDialog(ctk.CTkToplevel):
    """Create or edit a single time entry, with the input actually validated."""

    def __init__(self, parent, app: "AppController",
                 session: Optional[Session] = None, on_save=None):
        super().__init__(parent)
        self.app = app
        self.session = session
        self.on_save = on_save
        self._project_var = ctk.StringVar()
        self._task_var = ctk.StringVar()

        self.title("Edit session" if session else "Add time entry")
        self.geometry("460x480")
        self.resizable(False, False)
        self.configure(fg_color=T.BG_RAISED)
        self.transient(parent)
        self.grab_set()

        self._projects = app.db.get_projects(include_archived=True)
        self._build()
        self.bind("<Escape>", lambda _e: self.destroy())

    def _build(self):
        def label(text):
            ctk.CTkLabel(self, text=text, font=T.FONT_LABEL, text_color=T.TEXT_MUTED,
                         anchor="w").pack(fill="x", padx=24, pady=(12, 4))

        def entry():
            return ctk.CTkEntry(self, fg_color=T.BG_CARD, border_color=T.BORDER,
                                text_color=T.TEXT_BRIGHT, font=T.FONT_MONO,
                                height=34)

        label("Project")
        self._project_menu = ctk.CTkOptionMenu(
            self, variable=self._project_var, values=[p.name for p in self._projects]
            or ["—"], fg_color=T.BG_CARD, button_color=T.BORDER,
            button_hover_color=T.BG_HOVER, text_color=T.TEXT_BRIGHT,
            dropdown_fg_color=T.BG_CARD, dropdown_text_color=T.TEXT,
            font=T.FONT_BASE, height=34, command=lambda _v: self._reload_tasks())
        self._project_menu.pack(fill="x", padx=24)

        label("Task")
        self._task_menu = ctk.CTkOptionMenu(
            self, variable=self._task_var, values=["—"], fg_color=T.BG_CARD,
            button_color=T.BORDER, button_hover_color=T.BG_HOVER,
            text_color=T.TEXT_BRIGHT, dropdown_fg_color=T.BG_CARD,
            dropdown_text_color=T.TEXT, font=T.FONT_BASE, height=34)
        self._task_menu.pack(fill="x", padx=24)

        label("Started at  (YYYY-MM-DD HH:MM, your local time)")
        self._start = entry()
        self._start.pack(fill="x", padx=24)

        label("Duration  (minutes, or H:MM)")
        self._duration = entry()
        self._duration.pack(fill="x", padx=24)

        label("Notes")
        self._notes = ctk.CTkEntry(self, fg_color=T.BG_CARD, border_color=T.BORDER,
                                   text_color=T.TEXT, font=T.FONT_BASE, height=34)
        self._notes.pack(fill="x", padx=24)

        self._error = ctk.CTkLabel(self, text="", font=T.FONT_SMALL,
                                   text_color=T.DANGER, anchor="w", wraplength=400,
                                   justify="left")
        self._error.pack(fill="x", padx=24, pady=(10, 0))

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(fill="x", padx=24, pady=(4, 18), side="bottom")
        ctk.CTkButton(buttons, text="Cancel", width=100, height=36,
                      corner_radius=T.RADIUS_SM, **T.BTN_GHOST,
                      command=self.destroy).pack(side="left")
        ctk.CTkButton(buttons, text="Save", width=100, height=36,
                      corner_radius=T.RADIUS_SM, **T.BTN_PRIMARY,
                      command=self._save).pack(side="right")

        self._prefill()

    def _prefill(self):
        if self.session:
            project = next((p for p in self._projects
                            if p.id == self.session.project_id), None)
            if project:
                self._project_var.set(project.name)
            self._reload_tasks()
            self._task_var.set(self.session.subtask_name)
            started = parse_local(self.session.started_at)
            self._start.insert(0, started.strftime("%Y-%m-%d %H:%M") if started else "")
            self._duration.insert(0, format_hm(self.session.duration_seconds))
            self._notes.insert(0, self.session.notes or "")
        else:
            if self._projects:
                self._project_var.set(self._projects[0].name)
            self._reload_tasks()
            self._start.insert(0, now_local().strftime("%Y-%m-%d %H:%M"))
            self._duration.insert(0, "0:30")

    def _selected_project(self) -> Optional[Project]:
        return next((p for p in self._projects if p.name == self._project_var.get()),
                    None)

    def _reload_tasks(self):
        project = self._selected_project()
        tasks = self.app.db.get_subtasks(project.id) if project else []
        names = [t.name for t in tasks] or ["—"]
        self._task_menu.configure(values=names)
        if self._task_var.get() not in names:
            self._task_var.set(names[0])

    @staticmethod
    def _parse_duration(text: str) -> Optional[float]:
        """Accept ``90``, ``1:30`` or ``1.5h`` — all meaning ninety minutes."""
        text = (text or "").strip().lower()
        if not text:
            return None
        try:
            if ":" in text:
                hours, _, minutes = text.partition(":")
                return int(hours) * 3600 + int(minutes) * 60
            if text.endswith("h"):
                return float(text[:-1]) * 3600
            return float(text) * 60
        except ValueError:
            return None

    def _save(self):
        project = self._selected_project()
        if not project:
            self._error.configure(text="Choose a project.")
            return

        tasks = self.app.db.get_subtasks(project.id)
        task = next((t for t in tasks if t.name == self._task_var.get()), None)
        if not task:
            self._error.configure(text="Choose a task — this project has none yet.")
            return

        started = parse_datetime_input(self._start.get())
        if started is None:
            self._error.configure(
                text="Start time must look like 2026-08-12 09:30.")
            return

        seconds = self._parse_duration(self._duration.get())
        if seconds is None or seconds < 0:
            self._error.configure(
                text="Duration must be minutes (90), H:MM (1:30) or hours (1.5h).")
            return

        ended = started + timedelta(seconds=seconds)
        notes = self._notes.get().strip()

        if self.session:
            self.app.db.update_session(self.session.id, iso_utc(started),
                                       iso_utc(ended), seconds, notes)
        else:
            session_id = self.app.db.create_session(project.id, task.id, started)
            self.app.db.finalize_session(session_id, ended, seconds, 0.0)
            self.app.db.update_session_notes(session_id, notes)

        self.destroy()
        if self.on_save:
            self.on_save()


# ─────────────────────────────────────────────────────────────────────────────
# Reports
# ─────────────────────────────────────────────────────────────────────────────

class ReportsPanel(Panel):
    PERIODS = ["Today", "Week", "Month", "All time"]

    def __init__(self, parent, app: "AppController"):
        super().__init__(parent, app)
        self._period = "Week"
        self.grid_rowconfigure(1, weight=1)
        self._build()
        self.on_show()

    def _build(self):
        head = self.header(0, "Reports", "Where the time went")
        filters = ctk.CTkFrame(head, fg_color="transparent")
        filters.grid(row=0, column=1, rowspan=2, sticky="e")
        self._period_buttons = segmented(filters, self.PERIODS, self._period,
                                         self._set_period)

        self._scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._scroll.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 18))
        self._scroll.grid_columnconfigure(0, weight=1)

    def _set_period(self, period: str):
        self._period = period
        set_segmented(self._period_buttons, period)
        self.on_show()

    def on_show(self):
        self._load()

    def _load(self):
        for child in self._scroll.winfo_children():
            child.destroy()

        date_from, date_to = period_bounds(
            "All" if self._period == "All time" else self._period)
        rows = self.app.db.get_sessions_summary(date_from, date_to)

        if not rows:
            ctk.CTkLabel(self._scroll, text="No time recorded in this period.",
                         font=T.FONT_BASE, text_color=T.TEXT_MUTED).grid(
                row=0, column=0, pady=48)
            return

        by_project: dict[int, dict] = {}
        grand_total = 0.0
        for entry in rows:
            bucket = by_project.setdefault(entry["project_id"], {
                "name": entry["project_name"], "client": entry["client"],
                "color": entry["color"] or T.ACCENT, "tasks": [], "total": 0.0,
            })
            bucket["tasks"].append(entry)
            bucket["total"] += entry["total_seconds"]
            grand_total += entry["total_seconds"]

        index = 0
        total_card = ctk.CTkFrame(self._scroll, fg_color=T.ACCENT_BG,
                                  corner_radius=T.RADIUS_MD, height=76)
        total_card.grid(row=index, column=0, sticky="ew", pady=(0, 14))
        total_card.grid_propagate(False)
        total_card.grid_columnconfigure(0, weight=1)
        index += 1
        ctk.CTkLabel(total_card, text=period_label(date_from, date_to),
                     font=T.FONT_SMALL, text_color=T.TEXT_MUTED, anchor="w").grid(
            row=0, column=0, padx=20, pady=(14, 0), sticky="w")
        ctk.CTkLabel(total_card, text=f"{format_hours_decimal(grand_total)} hours",
                     font=T.FONT_HEADING, text_color=T.ACCENT, anchor="w").grid(
            row=1, column=0, padx=20, pady=(0, 14), sticky="w")
        ctk.CTkLabel(total_card, text=format_hm(grand_total), font=T.FONT_MONO_L,
                     text_color=T.TEXT_BRIGHT, anchor="e").grid(
            row=0, column=1, rowspan=2, padx=20, sticky="e")

        for bucket in sorted(by_project.values(), key=lambda b: -b["total"]):
            index = self._add_project(index, bucket, grand_total)

    def _add_project(self, index: int, bucket: dict, grand_total: float) -> int:
        card = ctk.CTkFrame(self._scroll, fg_color=T.BG_CARD,
                            corner_radius=T.RADIUS_MD)
        card.grid(row=index, column=0, sticky="ew", pady=(0, 10))
        card.grid_columnconfigure(1, weight=1)
        index += 1

        share = bucket["total"] / grand_total if grand_total else 0.0

        ctk.CTkLabel(card, text="●", text_color=bucket["color"], font=T.FONT_LARGE,
                     width=20).grid(row=0, column=0, padx=(14, 6), pady=(14, 0))
        ctk.CTkLabel(card, text=bucket["name"], font=T.FONT_MED,
                     text_color=T.TEXT_BRIGHT, anchor="w").grid(
            row=0, column=1, sticky="w", pady=(14, 0))
        ctk.CTkLabel(card, text=f"{share * 100:.0f}%", font=T.FONT_SMALL,
                     text_color=T.TEXT_MUTED, width=44, anchor="e").grid(
            row=0, column=2, padx=6, pady=(14, 0))
        ctk.CTkLabel(card, text=format_hm(bucket["total"]), font=T.FONT_MONO_L,
                     text_color=T.ACCENT, width=76, anchor="e").grid(
            row=0, column=3, padx=(6, 16), pady=(14, 0))

        # A place()d bar with relwidth resizes with the window.  The previous
        # version measured the track before layout had run, so every bar came
        # out at the minimum width regardless of its share.
        track = ctk.CTkFrame(card, fg_color=T.BG_BASE, height=6,
                             corner_radius=3)
        track.grid(row=1, column=0, columnspan=4, sticky="ew", padx=16, pady=(8, 0))
        track.grid_propagate(False)
        ctk.CTkFrame(track, fg_color=bucket["color"], corner_radius=3).place(
            relx=0, rely=0, relwidth=max(share, 0.004), relheight=1)

        for task in sorted(bucket["tasks"], key=lambda t: -t["total_seconds"]):
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.grid(row=card.grid_size()[1], column=0, columnspan=4, sticky="ew",
                     padx=16, pady=1)
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(row, text=task["subtask_name"], font=T.FONT_SMALL,
                         text_color=T.TEXT, anchor="w").grid(row=0, column=0,
                                                             sticky="ew", padx=(20, 0))
            ctk.CTkLabel(row, text=f"{task['session_count']}×", font=T.FONT_TINY,
                         text_color=T.TEXT_DIM, width=34, anchor="e").grid(
                row=0, column=1, padx=4)
            ctk.CTkLabel(row, text=format_hm(task["total_seconds"]),
                         font=T.FONT_MONO, text_color=T.TEXT_MUTED, width=72,
                         anchor="e").grid(row=0, column=2)

        ctk.CTkFrame(card, fg_color="transparent", height=10).grid(
            row=card.grid_size()[1], column=0)
        return index


# ─────────────────────────────────────────────────────────────────────────────
# Exports
# ─────────────────────────────────────────────────────────────────────────────

class ExportsPanel(Panel):
    def __init__(self, parent, app: "AppController"):
        super().__init__(parent, app)
        self.grid_rowconfigure(1, weight=1)
        self._project_var = ctk.StringVar(value="All projects")
        self._include_sessions = ctk.BooleanVar(value=True)
        self._build()
        self.on_show()

    def _build(self):
        self.header(0, "Exports", "Produce a timesheet to send on")

        form = ctk.CTkScrollableFrame(self, fg_color="transparent")
        form.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 18))
        form.grid_columnconfigure(0, weight=1)
        form.grid_columnconfigure(1, weight=1)

        def label(text, row, column=0, span=1):
            ctk.CTkLabel(form, text=text, font=T.FONT_LABEL, text_color=T.TEXT_MUTED,
                         anchor="w").grid(row=row, column=column, columnspan=span,
                                          sticky="w", pady=(12, 4))

        def field(row, column=0, span=1):
            widget = ctk.CTkEntry(form, fg_color=T.BG_CARD, border_color=T.BORDER,
                                  text_color=T.TEXT_BRIGHT, font=T.FONT_BASE,
                                  height=36)
            widget.grid(row=row, column=column, columnspan=span, sticky="ew",
                        padx=(0, 8 if column == 0 and span == 1 else 0))
            return widget

        row = 0
        label("Prepared by", row)
        label("Organisation (optional)", row, 1)
        row += 1
        self._worker = field(row, 0)
        self._company = field(row, 1)
        row += 1

        label("Project", row, span=2)
        row += 1
        self._project_menu = ctk.CTkOptionMenu(
            form, variable=self._project_var, values=["All projects"],
            fg_color=T.BG_CARD, button_color=T.BORDER, button_hover_color=T.BG_HOVER,
            text_color=T.TEXT_BRIGHT, dropdown_fg_color=T.BG_CARD,
            dropdown_text_color=T.TEXT, font=T.FONT_BASE, height=36)
        self._project_menu.grid(row=row, column=0, columnspan=2, sticky="ew")
        row += 1

        label("From  (YYYY-MM-DD, blank for no limit)", row)
        label("To  (YYYY-MM-DD, inclusive)", row, 1)
        row += 1
        self._date_from = field(row, 0)
        self._date_to = field(row, 1)
        row += 1

        quick = ctk.CTkFrame(form, fg_color="transparent")
        quick.grid(row=row, column=0, columnspan=2, sticky="w", pady=(10, 0))
        for index, name in enumerate(["This week", "Last week", "This month",
                                      "Last month", "Clear"]):
            ctk.CTkButton(quick, text=name, width=88, height=28, font=T.FONT_SMALL,
                          corner_radius=T.RADIUS_SM, **T.BTN_GHOST,
                          command=lambda n=name: self._quick_range(n)).grid(
                row=0, column=index, padx=(0, 6))
        row += 1

        ctk.CTkCheckBox(form, text="Include the full session log in the PDF",
                        variable=self._include_sessions, font=T.FONT_SMALL,
                        text_color=T.TEXT, fg_color=T.ACCENT,
                        hover_color=T.ACCENT_HOVER, checkbox_width=18,
                        checkbox_height=18).grid(row=row, column=0, columnspan=2,
                                                 sticky="w", pady=(16, 0))
        row += 1

        buttons = ctk.CTkFrame(form, fg_color="transparent")
        buttons.grid(row=row, column=0, columnspan=2, sticky="w", pady=(18, 0))
        button_kw = {"width": 132, "height": 42, "font": T.FONT_MED,
                         "corner_radius": T.RADIUS_MD}
        ctk.CTkButton(buttons, text="PDF timesheet", **button_kw, **T.BTN_PRIMARY,
                      command=lambda: self._export("pdf")).grid(row=0, column=0,
                                                                padx=(0, 8))
        ctk.CTkButton(buttons, text="Excel workbook", **button_kw, **T.BTN_GHOST,
                      command=lambda: self._export("xlsx")).grid(row=0, column=1,
                                                                 padx=(0, 8))
        ctk.CTkButton(buttons, text="CSV", **button_kw, **T.BTN_GHOST,
                      command=lambda: self._export("csv")).grid(row=0, column=2)
        row += 1

        self._status = ctk.CTkLabel(form, text="", font=T.FONT_BASE,
                                    text_color=T.SUCCESS, anchor="w", wraplength=560,
                                    justify="left")
        self._status.grid(row=row, column=0, columnspan=2, sticky="w", pady=(14, 0))
        row += 1

        ctk.CTkLabel(form, text="Recent exports", font=T.FONT_LARGE,
                     text_color=T.TEXT_BRIGHT, anchor="w").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(24, 6))
        row += 1
        self._history = ctk.CTkFrame(form, fg_color="transparent")
        self._history.grid(row=row, column=0, columnspan=2, sticky="ew")
        self._history.grid_columnconfigure(0, weight=1)

    # ── Data ──────────────────────────────────────────────────────────────

    def on_show(self):
        projects = self.app.db.get_projects()
        options = ["All projects"] + [p.name for p in projects]
        self._project_menu.configure(values=options)
        if self._project_var.get() not in options:
            self._project_var.set("All projects")

        for widget, key in ((self._worker, "worker_name"),
                            (self._company, "company_name")):
            widget.delete(0, "end")
            widget.insert(0, self.app.db.get_setting(key, ""))
        self._load_history()

    def _quick_range(self, name: str):
        today = now_local()
        if name == "Clear":
            start = end = None
        elif name == "This week":
            start = today - timedelta(days=today.weekday())
            end = today
        elif name == "Last week":
            start = today - timedelta(days=today.weekday() + 7)
            end = start + timedelta(days=6)
        elif name == "This month":
            start = today.replace(day=1)
            end = today
        else:  # Last month
            first = today.replace(day=1)
            end = first - timedelta(days=1)
            start = end.replace(day=1)

        self._date_from.delete(0, "end")
        self._date_to.delete(0, "end")
        if start:
            self._date_from.insert(0, start.strftime("%Y-%m-%d"))
            self._date_to.insert(0, end.strftime("%Y-%m-%d"))

    def _resolve_range(self):
        """Return (from, to, error).  Dates are validated, not silently ignored."""
        raw_from = self._date_from.get().strip()
        raw_to = self._date_to.get().strip()

        start = end = None
        if raw_from:
            parsed = parse_date_input(raw_from)
            if parsed is None:
                return None, None, f"“{raw_from}” is not a date. Use YYYY-MM-DD."
            start = local_day_start(parsed.astimezone()).isoformat()
        if raw_to:
            parsed = parse_date_input(raw_to)
            if parsed is None:
                return None, None, f"“{raw_to}” is not a date. Use YYYY-MM-DD."
            # Inclusive: everything up to the end of that day.
            end = local_day_end(parsed.astimezone()).isoformat()
        if start and end and start >= end:
            return None, None, "The “from” date must come before the “to” date."
        return start, end, None

    def _export(self, fmt: str):
        date_from, date_to, error = self._resolve_range()
        if error:
            self._status.configure(text=error, text_color=T.DANGER)
            return

        worker = self._worker.get().strip()
        company = self._company.get().strip()
        self.app.db.set_setting("worker_name", worker)
        self.app.db.set_setting("company_name", company)

        projects = self.app.db.get_projects(include_archived=True)
        selection = self._project_var.get()
        if selection != "All projects":
            projects = [p for p in projects if p.name == selection]
        if not projects:
            self._status.configure(text="No projects to export.", text_color=T.DANGER)
            return
        project_ids = [p.id for p in projects]

        suggested = default_filename(
            f"{APP_NAME}_Timesheet"
            if selection == "All projects"
            else f"{APP_NAME}_{selection[:24]}", fmt)
        target = self._ask_destination(fmt, suggested)
        if not target:
            return

        try:
            if fmt == "pdf":
                from ..exports.pdf_exp import export_pdf
                export_pdf(self.app.db, projects, date_from, date_to, worker,
                           str(target), company=company,
                           include_sessions=self._include_sessions.get())
            elif fmt == "xlsx":
                from ..exports.xlsx_exp import export_xlsx
                export_xlsx(self.app.db, projects, date_from, date_to, worker,
                            str(target), company=company)
            else:
                from ..exports.csv_exp import export_csv
                export_csv(self.app.db, project_ids, date_from, date_to, str(target))
        except Exception as error:  # surfaced, not swallowed
            self._status.configure(text=f"Export failed: {error}",
                                   text_color=T.DANGER)
            return

        self.app.db.set_setting("last_export_dir", str(target.parent))
        self.app.db.save_export(fmt.upper(), str(target), project_ids,
                                date_from or "", date_to or "")
        self._status.configure(text=f"Saved to {target}", text_color=T.SUCCESS)
        self._load_history()
        self.notify("Export complete", "ok")

    def _ask_destination(self, fmt: str, suggested: str) -> Optional[Path]:
        types = {
            "pdf": [("PDF document", "*.pdf")],
            "xlsx": [("Excel workbook", "*.xlsx")],
            "csv": [("CSV file", "*.csv")],
        }[fmt]
        initial = self.app.db.get_setting("last_export_dir", "") or str(
            self.app.exports_dir)
        chosen = filedialog.asksaveasfilename(
            parent=self, title="Save export as", initialdir=initial,
            initialfile=suggested, defaultextension=f".{fmt}",
            filetypes=types + [("All files", "*.*")],
        )
        return Path(chosen) if chosen else None

    def _load_history(self):
        for child in self._history.winfo_children():
            child.destroy()

        entries = self.app.db.get_export_history(8)
        if not entries:
            ctk.CTkLabel(self._history, text="Nothing exported yet.",
                         font=T.FONT_SMALL, text_color=T.TEXT_DIM,
                         anchor="w").grid(row=0, column=0, sticky="w", pady=4)
            return

        for index, entry in enumerate(entries):
            path = Path(entry["filename"])
            exists = path.exists()
            row = ctk.CTkFrame(self._history, fg_color=T.BG_CARD,
                               corner_radius=T.RADIUS_SM, height=38)
            row.grid(row=index, column=0, sticky="ew", pady=2)
            row.grid_columnconfigure(1, weight=1)
            row.grid_propagate(False)

            ctk.CTkLabel(row, text=entry["export_type"], font=T.FONT_MONO,
                         text_color=T.ACCENT, width=48).grid(row=0, column=0, padx=10)
            ctk.CTkLabel(row, text=path.name, font=T.FONT_SMALL, anchor="w",
                         text_color=T.TEXT if exists else T.TEXT_DIM).grid(
                row=0, column=1, sticky="w")
            ctk.CTkLabel(row, text=fmt_datetime(entry["created_at"], "%d %b %H:%M"),
                         font=T.FONT_SMALL, text_color=T.TEXT_MUTED).grid(
                row=0, column=2, padx=10)
            ctk.CTkButton(row, text="Open" if exists else "Missing", width=64,
                          height=26, font=T.FONT_SMALL, corner_radius=T.RADIUS_SM,
                          state="normal" if exists else "disabled", **T.BTN_GHOST,
                          command=lambda p=path: self._open(p)).grid(
                row=0, column=3, padx=(0, 8))

    def _open(self, path: Path):
        if not open_path(path):
            self.notify(f"Could not open {path.name}", "error")


# ─────────────────────────────────────────────────────────────────────────────
# Settings
# ─────────────────────────────────────────────────────────────────────────────

class SettingsPanel(Panel):
    def __init__(self, parent, app: "AppController"):
        super().__init__(parent, app)
        self.grid_rowconfigure(1, weight=1)
        self._build()

    def _build(self):
        self.header(0, "Settings", "Your details, your data")

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 18))
        scroll.grid_columnconfigure(0, weight=1)

        row = 0

        def section(title: str, nonlocal_row: int) -> int:
            ctk.CTkLabel(scroll, text=title, font=T.FONT_TITLE,
                         text_color=T.TEXT_BRIGHT, anchor="w").grid(
                row=nonlocal_row, column=0, sticky="w", pady=(18, 8))
            return nonlocal_row + 1

        def card(nonlocal_row: int, title: str, description: str):
            frame = ctk.CTkFrame(scroll, fg_color=T.BG_CARD,
                                 corner_radius=T.RADIUS_MD)
            frame.grid(row=nonlocal_row, column=0, sticky="ew", pady=3)
            frame.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(frame, text=title, font=T.FONT_BASE, text_color=T.TEXT,
                         anchor="w").grid(row=0, column=0, padx=16, pady=(12, 0),
                                          sticky="w")
            ctk.CTkLabel(frame, text=description, font=T.FONT_TINY,
                         text_color=T.TEXT_MUTED, anchor="w", justify="left").grid(
                row=1, column=0, padx=16, pady=(1, 12), sticky="w")
            return frame

        row = section("Your details", row)

        details = ctk.CTkFrame(scroll, fg_color=T.BG_CARD, corner_radius=T.RADIUS_MD)
        details.grid(row=row, column=0, sticky="ew", pady=3)
        details.grid_columnconfigure(1, weight=1)
        row += 1

        ctk.CTkLabel(details, text="Name on reports", font=T.FONT_BASE,
                     text_color=T.TEXT, width=170, anchor="w").grid(
            row=0, column=0, padx=16, pady=(14, 6), sticky="w")
        self._worker = ctk.CTkEntry(details, fg_color=T.BG_BASE,
                                    border_color=T.BORDER, text_color=T.TEXT_BRIGHT,
                                    font=T.FONT_BASE, height=32)
        self._worker.grid(row=0, column=1, padx=(0, 16), pady=(14, 6), sticky="ew")

        ctk.CTkLabel(details, text="Organisation", font=T.FONT_BASE,
                     text_color=T.TEXT, width=170, anchor="w").grid(
            row=1, column=0, padx=16, pady=(0, 14), sticky="w")
        self._company = ctk.CTkEntry(details, fg_color=T.BG_BASE,
                                     border_color=T.BORDER, text_color=T.TEXT_BRIGHT,
                                     font=T.FONT_BASE, height=32)
        self._company.grid(row=1, column=1, padx=(0, 16), pady=(0, 14), sticky="ew")

        ctk.CTkButton(scroll, text="Save details", **T.BTN_PRIMARY, height=36,
                      width=140, corner_radius=T.RADIUS_SM,
                      command=self._save_details).grid(row=row, column=0, sticky="w",
                                                       pady=(10, 0))
        row += 1

        row = section("Data and backups", row)

        backup_card = card(row, "Back up now",
                           "Writes a JSON snapshot of everything you have recorded.")
        ctk.CTkButton(backup_card, text="Back up", width=110, height=32,
                      corner_radius=T.RADIUS_SM, **T.BTN_GHOST,
                      command=self._backup).grid(row=0, column=1, rowspan=2,
                                                 padx=16, pady=12)
        row += 1

        restore_card = card(row, "Restore from a backup",
                            "Replaces everything currently recorded. "
                            "The current data is copied aside first.")
        ctk.CTkButton(restore_card, text="Restore…", width=110, height=32,
                      corner_radius=T.RADIUS_SM, **T.BTN_GHOST,
                      command=self._restore).grid(row=0, column=1, rowspan=2,
                                                  padx=16, pady=12)
        row += 1

        folder_card = card(row, "Data folder",
                           str(self.app.data_dir))
        ctk.CTkButton(folder_card, text="Open", width=110, height=32,
                      corner_radius=T.RADIUS_SM, **T.BTN_GHOST,
                      command=lambda: open_path(self.app.data_dir)).grid(
            row=0, column=1, rowspan=2, padx=16, pady=12)
        row += 1

        row = section("About", row)
        about = ctk.CTkFrame(scroll, fg_color=T.BG_CARD, corner_radius=T.RADIUS_MD)
        about.grid(row=row, column=0, sticky="ew", pady=3)
        about.grid_columnconfigure(0, weight=1)
        row += 1
        ctk.CTkLabel(about, text=f"{APP_NAME} {__version__}", font=T.FONT_MED,
                     text_color=T.TEXT_BRIGHT, anchor="w").grid(
            row=0, column=0, padx=16, pady=(14, 0), sticky="w")
        ctk.CTkLabel(about,
                     text="Times are recorded in UTC and shown in your local "
                          "time zone.\nCtrl+S saves · Ctrl+Z undoes · Ctrl+F searches.",
                     font=T.FONT_TINY, text_color=T.TEXT_MUTED, anchor="w",
                     justify="left").grid(row=1, column=0, padx=16, pady=(4, 14),
                                          sticky="w")

        self._status = ctk.CTkLabel(scroll, text="", font=T.FONT_BASE,
                                    text_color=T.SUCCESS, anchor="w")
        self._status.grid(row=row, column=0, sticky="w", pady=10)

        self.on_show()

    def on_show(self):
        for widget, key in ((self._worker, "worker_name"),
                            (self._company, "company_name")):
            widget.delete(0, "end")
            widget.insert(0, self.app.db.get_setting(key, ""))

    def _flash(self, message: str, tone: str = "ok"):
        self._status.configure(text=message,
                               text_color=T.SUCCESS if tone == "ok" else T.DANGER)
        self.after(4000, lambda: self._status.configure(text=""))

    def _save_details(self):
        self.app.db.set_setting("worker_name", self._worker.get().strip())
        self.app.db.set_setting("company_name", self._company.get().strip())
        self._flash("Saved.")

    def _backup(self):
        try:
            path = self.app.backup.make_backup("manual")
        except OSError as error:
            self._flash(f"Backup failed: {error}", "error")
            return
        self._flash(f"Backed up to {path.name}")

    def _restore(self):
        chosen = filedialog.askopenfilename(
            parent=self, title="Choose a backup to restore",
            initialdir=str(self.app.backup.backup_dir),
            filetypes=[("WorkTrack backup", "*.json"), ("All files", "*.*")],
        )
        if not chosen:
            return
        if self.app.timer.is_active:
            self._flash("Stop the running timer before restoring.", "error")
            return
        if not _confirm(self, "Restore backup",
                        f"Replace everything currently recorded with the contents "
                        f"of {Path(chosen).name}?\n\n"
                        "Your current data is copied aside first, into the backups "
                        "folder."):
            return
        if self.app.backup.restore_backup(Path(chosen)):
            self._flash("Restored.")
            board = self.dashboard
            if board:
                board.rebuild_panels(keep="Settings")
        else:
            self._flash("That file could not be restored.", "error")
