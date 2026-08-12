"""
Time handling for WorkTrack.

One rule, applied everywhere: **timestamps are stored in UTC and displayed in
local time**.  Storing UTC keeps durations correct across daylight-saving
transitions and time-zone changes; displaying local time is what a person
filling in a timesheet expects to see.

Every stored timestamp is a canonical ISO-8601 string with an explicit UTC
offset and no microseconds, e.g. ``2026-06-01T09:00:00+00:00``.  The fixed
width matters: the database filters date ranges with string comparisons, which
are only equivalent to chronological comparisons when every value is formatted
identically.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

UTC = timezone.utc


# ── Current time ─────────────────────────────────────────────────────────────

def now_utc() -> datetime:
    """Timezone-aware current time in UTC, truncated to whole seconds."""
    return datetime.now(UTC).replace(microsecond=0)


def now_local() -> datetime:
    """Timezone-aware current time in the machine's local zone."""
    return datetime.now().astimezone()


# ── Conversion ───────────────────────────────────────────────────────────────

def to_utc(dt: datetime) -> datetime:
    """Convert any datetime to aware UTC.  Naive input is assumed to be UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC, microsecond=0)
    return dt.astimezone(UTC).replace(microsecond=0)


def to_local(dt: datetime) -> datetime:
    """Convert any datetime to the machine's local zone."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone()


def iso_utc(dt: datetime) -> str:
    """Serialise a datetime to the canonical stored form."""
    return to_utc(dt).isoformat()


def parse_utc(value: Optional[str]) -> Optional[datetime]:
    """
    Parse a stored timestamp into aware UTC, or None if it cannot be read.

    Naive values are treated as UTC so that databases written by earlier
    versions — which stored ``datetime.utcnow()`` without an offset — are read
    back at the correct instant.
    """
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return to_utc(datetime.fromisoformat(text))
    except ValueError:
        # Tolerate a trailing timestamp fragment, e.g. a truncated edit.
        try:
            return to_utc(datetime.fromisoformat(text[:19]))
        except ValueError:
            return None


def parse_local(value: Optional[str]) -> Optional[datetime]:
    """Parse a stored timestamp and return it in local time."""
    dt = parse_utc(value)
    return to_local(dt) if dt else None


# ── Date-range boundaries ────────────────────────────────────────────────────
#
# Ranges are anchored to local calendar days — "today" means the user's today,
# not UTC's — then converted to UTC for querying.

def local_day_start(day: datetime) -> datetime:
    """Midnight at the start of ``day``, in local time, as aware UTC."""
    local = to_local(day) if day.tzinfo else day.astimezone()
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return to_utc(midnight)


def local_day_end(day: datetime) -> datetime:
    """Midnight at the *end* of ``day`` (exclusive bound), as aware UTC."""
    return local_day_start(day) + timedelta(days=1)


def parse_date_input(text: str) -> Optional[datetime]:
    """
    Parse a user-typed date.

    Accepts ``YYYY-MM-DD``, ``DD/MM/YYYY`` and ``DD-MM-YYYY``.  Returns a naive
    local-midnight datetime, or None if the text is not a date the user meant.
    """
    text = (text or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_datetime_input(text: str) -> Optional[datetime]:
    """
    Parse a user-typed date-and-time in **local** time, returned as aware UTC.

    Accepts ``YYYY-MM-DD HH:MM[:SS]`` with either a space or ``T`` separator.
    """
    text = (text or "").strip().replace("T", " ")
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            naive_local = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return to_utc(naive_local.astimezone())
    return None


# ── Display formatting ───────────────────────────────────────────────────────

def fmt_date(value: Optional[str], fmt: str = "%d %b %Y") -> str:
    """Format a stored timestamp as a local date."""
    dt = parse_local(value)
    return dt.strftime(fmt) if dt else ""


def fmt_time(value: Optional[str], fmt: str = "%H:%M") -> str:
    """Format a stored timestamp as a local time of day."""
    dt = parse_local(value)
    return dt.strftime(fmt) if dt else ""


def fmt_datetime(value: Optional[str], fmt: str = "%d %b %Y  %H:%M") -> str:
    """Format a stored timestamp as a local date and time."""
    dt = parse_local(value)
    return dt.strftime(fmt) if dt else ""
