"""
Smoke tests that build the real windows.

These do not assert on appearance — they assert that every panel can be
constructed, switched to, and refreshed against real data without raising.
That is the class of breakage a pure-logic suite cannot see.
"""
from datetime import timedelta

import pytest

from src.timeutil import iso_utc, now_utc

pytest.importorskip("customtkinter", reason="the UI needs customtkinter")
tkinter = pytest.importorskip("tkinter")


@pytest.fixture(scope="session")
def tk_root():
    """
    One Tk root for the whole session.

    Tk is not happy being torn down and stood back up repeatedly inside a
    single process, so the windows under test are Toplevels on a shared root.
    """
    import customtkinter as ctk

    try:
        root = ctk.CTk()
    except tkinter.TclError as error:
        pytest.skip(f"no usable display: {error}")
    root.withdraw()
    yield root
    try:
        root.destroy()
    except tkinter.TclError:
        pass


@pytest.fixture
def controller(tmp_path, tk_root):
    """An AppController pointed at a throwaway data directory."""
    from src.ui.app import AppController

    app = AppController(data_directory=tmp_path)

    project_id = app.db.create_project("Aqueduct", "Fulcrum", "Corridor survey.")
    task_id = app.db.create_subtask(project_id, "Survey", 0)
    start = now_utc() - timedelta(hours=3)
    app.db._conn.execute(
        "INSERT INTO sessions(project_id,subtask_id,started_at,ended_at,"
        "duration_seconds,notes,is_running) VALUES(?,?,?,?,?,'note',0)",
        (project_id, task_id, iso_utc(start), iso_utc(start + timedelta(hours=2)),
         7200),
    )
    app.db.commit()

    yield app
    app.db.close()


@pytest.fixture
def windows(controller, tk_root):
    from src.ui.dashboard import DashboardWindow
    from src.ui.widget import WidgetWindow

    controller._root = tk_root
    controller.widget = WidgetWindow(tk_root, controller)
    controller.dashboard = DashboardWindow(tk_root, controller)
    tk_root.update()

    yield controller

    for window in (controller.dashboard, controller.widget):
        try:
            window.destroy()
        except tkinter.TclError:
            pass
    controller.dashboard = None
    controller.widget = None
    tk_root.update()


def test_every_panel_builds_and_refreshes(windows):
    dashboard = windows.dashboard
    for name, _hint in dashboard.NAV_ITEMS:
        dashboard._switch_panel(name)
        dashboard.update()
        dashboard.refresh_current_panel()
        dashboard.update()


def test_selecting_a_project_shows_its_tasks(windows):
    dashboard = windows.dashboard
    dashboard._switch_panel("Projects")
    panel = dashboard._panels["Projects"]
    project = windows.db.get_projects()[0]

    panel._select_project(project.id)
    dashboard.update()
    assert panel._selected_project_id == project.id


def test_search_filters_the_project_list(windows):
    dashboard = windows.dashboard
    dashboard._switch_panel("Projects")
    panel = dashboard._panels["Projects"]

    panel.apply_search("no such project")
    dashboard.update()
    panel.apply_search("aqueduct")
    dashboard.update()
    assert panel._search_filter == "aqueduct"


def test_timer_controls_drive_both_windows(windows):
    project = windows.db.get_projects()[0]
    task = windows.db.get_subtasks(project.id)[0]

    windows.timer.start(project.id, task.id)
    windows._root.update()
    assert windows.timer.is_running

    windows.timer.pause()
    windows._root.update()
    windows.timer.stop()
    windows._root.update()
    assert not windows.timer.is_active


def test_history_filters_cover_every_period(windows):
    dashboard = windows.dashboard
    dashboard._switch_panel("History")
    panel = dashboard._panels["History"]
    for period in panel.PERIODS:
        panel._set_period(period)
        dashboard.update()


def test_report_periods_all_render(windows):
    dashboard = windows.dashboard
    dashboard._switch_panel("Reports")
    panel = dashboard._panels["Reports"]
    for period in panel.PERIODS:
        panel._set_period(period)
        dashboard.update()


def test_export_panel_rejects_bad_dates(windows):
    dashboard = windows.dashboard
    dashboard._switch_panel("Exports")
    panel = dashboard._panels["Exports"]

    panel._date_from.insert(0, "not a date")
    start, end, error = panel._resolve_range()
    assert error and start is None

    panel._date_from.delete(0, "end")
    panel._date_from.insert(0, "2026-06-30")
    panel._date_to.insert(0, "2026-06-01")
    _start, _end, error = panel._resolve_range()
    assert "before" in error


def test_export_panel_accepts_an_inclusive_range(windows):
    dashboard = windows.dashboard
    dashboard._switch_panel("Exports")
    panel = dashboard._panels["Exports"]

    panel._date_from.insert(0, "2026-06-01")
    panel._date_to.insert(0, "2026-06-30")
    start, end, error = panel._resolve_range()
    assert error is None and start < end
    # "to 30 June" must include everything recorded on the 30th.
    assert end > "2026-06-30T00:00:00+00:00"


def test_quick_ranges_fill_both_date_fields(windows):
    dashboard = windows.dashboard
    dashboard._switch_panel("Exports")
    panel = dashboard._panels["Exports"]

    for name in ["This week", "Last week", "This month", "Last month"]:
        panel._quick_range(name)
        assert panel._date_from.get() and panel._date_to.get()
        _start, _end, error = panel._resolve_range()
        assert error is None, f"{name} produced {error}"

    panel._quick_range("Clear")
    assert panel._date_from.get() == "" and panel._date_to.get() == ""


def test_session_dialog_parses_the_duration_forms():
    from src.ui.dashboard import SessionDialog

    assert SessionDialog._parse_duration("90") == 5400
    assert SessionDialog._parse_duration("1:30") == 5400
    assert SessionDialog._parse_duration("1.5h") == 5400
    assert SessionDialog._parse_duration("rubbish") is None
    assert SessionDialog._parse_duration("") is None


def test_toast_messages_do_not_raise(windows):
    windows.dashboard.notify("Something happened", "ok")
    windows.dashboard.update()


def test_history_search_and_paging_controls(windows):
    dashboard = windows.dashboard
    dashboard._switch_panel("History")
    panel = dashboard._panels["History"]
    panel.PAGE_SIZE = 1
    session = windows.db.get_sessions()[0]
    windows.db.restore_session(type(session)(**{**vars(session), "id": session.id + 1}))
    panel._set_period("All")
    assert panel._next.cget("state") == "normal"
    panel._page(1)
    assert panel._offset == 1
    assert panel._next.cget("state") == "disabled"
    panel._search.insert(0, "no match")
    panel._search_history()
    assert panel._offset == 0
    assert panel._total.cget("text") == "—"
    panel._search.delete(0, "end")
    panel._search.insert(0, "note")
    panel._search_history()
    assert "Page total" in panel._total.cget("text")
    dashboard.update()
