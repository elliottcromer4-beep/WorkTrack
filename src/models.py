"""Data records and duration formatting."""
from dataclasses import dataclass
from typing import Optional


@dataclass
class Project:
    id: int
    name: str
    client: str = ""
    notes: str = ""
    created_at: str = ""
    archived: bool = False
    color: str = "#4DABF7"


@dataclass
class Subtask:
    id: int
    project_id: int
    name: str
    created_at: str = ""
    position: int = 0
    deleted: bool = False


@dataclass
class Session:
    id: int
    subtask_id: int
    project_id: int
    started_at: str
    ended_at: Optional[str] = None
    duration_seconds: float = 0.0
    paused_duration_seconds: float = 0.0
    notes: str = ""
    is_running: bool = False
    # Joined display fields
    project_name: str = ""
    subtask_name: str = ""
    client: str = ""


@dataclass
class TimerStateRecord:
    session_id: Optional[int] = None
    status: str = "stopped"
    project_id: Optional[int] = None
    subtask_id: Optional[int] = None
    started_at: Optional[str] = None
    paused_at: Optional[str] = None
    paused_duration_seconds: float = 0.0
    #: When the timer last recorded that it was still being watched.
    updated_at: Optional[str] = None


# ── Duration formatting ──────────────────────────────────────────────────────
#
# Three audiences, three formats:
#   format_duration        — the running clock, seconds visible
#   format_duration_human  — at a glance in the UI ("7h 15m")
#   format_hm / format_hours_decimal — timesheets and invoices

def _clamp(seconds: float) -> int:
    return int(seconds) if seconds > 0 else 0


def format_duration(seconds: float) -> str:
    """Live timer readout — ``H:MM:SS`` once past an hour, otherwise ``MM:SS``."""
    total = _clamp(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def format_duration_human(seconds: float) -> str:
    """Readable duration for the UI, e.g. ``7h 15m``, ``45m``, ``12s``."""
    total = _clamp(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    if m:
        return f"{m}m"
    return f"{s}s"


def format_hm(seconds: float) -> str:
    """
    Timesheet duration as ``H:MM``, rounded to the nearest minute.

    This is the form clients expect on an invoice line; it never abbreviates
    away the hour, so a column of values stays vertically comparable.
    """
    minutes = int(round(_clamp(seconds) / 60.0))
    return f"{minutes // 60}:{minutes % 60:02d}"


def format_hours_decimal(seconds: float, places: int = 2) -> str:
    """Duration in decimal hours, e.g. ``7.25`` — the billing unit."""
    return f"{_clamp(seconds) / 3600.0:.{places}f}"
