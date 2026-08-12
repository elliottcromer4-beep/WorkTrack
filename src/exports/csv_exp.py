"""CSV export — one row per session, for spreadsheets and accounting imports."""
import csv
from typing import Optional

from ..database import Database
from ..models import format_hm, format_hours_decimal
from ..timeutil import fmt_date, fmt_time

HEADERS = [
    "Date", "Start Time", "End Time",
    "Project", "Client", "Task",
    "Duration (seconds)", "Duration (H:MM)", "Duration (hours)",
    "Notes",
]


def export_csv(
    db: Database,
    project_ids: list[int],
    date_from: Optional[str],
    date_to: Optional[str],
    output_path: str,
):
    sessions = db.get_sessions(project_ids=project_ids, date_from=date_from,
                               date_to=date_to)
    sessions.sort(key=lambda s: s.started_at or "")

    # utf-8-sig so Excel on Windows opens accented names correctly.
    with open(output_path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADERS)
        for s in sessions:
            writer.writerow([
                fmt_date(s.started_at, "%Y-%m-%d"),
                fmt_time(s.started_at, "%H:%M:%S"),
                fmt_time(s.ended_at, "%H:%M:%S"),
                s.project_name,
                s.client,
                s.subtask_name,
                int(s.duration_seconds),
                format_hm(s.duration_seconds),
                format_hours_decimal(s.duration_seconds),
                s.notes or "",
            ])
