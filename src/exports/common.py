"""Helpers shared by every export format."""
from typing import Optional

from ..timeutil import fmt_date


def period_label(date_from: Optional[str], date_to: Optional[str]) -> str:
    """Describe a reporting period the way it should read on a cover page."""
    start = fmt_date(date_from) if date_from else ""
    end = fmt_date(date_to) if date_to else ""
    if start and end:
        return f"{start} – {end}"
    if start:
        return f"From {start}"
    if end:
        return f"Up to {end}"
    return "All recorded time"


def default_filename(prefix: str, extension: str, when=None) -> str:
    """Timestamped filename, e.g. ``WorkTrack_Timesheet_20260812_2347.pdf``."""
    from ..timeutil import now_local
    stamp = (when or now_local()).strftime("%Y%m%d_%H%M")
    return f"{prefix}_{stamp}.{extension}"
