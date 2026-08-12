from datetime import datetime, timedelta, timezone

import pytest

from src.timeutil import (
    fmt_date,
    iso_utc,
    local_day_end,
    local_day_start,
    now_utc,
    parse_date_input,
    parse_datetime_input,
    parse_utc,
    to_local,
    to_utc,
)


def test_naive_timestamps_are_read_as_utc():
    """Databases written by earlier versions stored naive utcnow() strings."""
    parsed = parse_utc("2026-06-01T09:00:00")
    assert parsed == datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)


def test_offset_timestamps_round_trip():
    original = "2026-06-01T09:00:00+00:00"
    assert iso_utc(parse_utc(original)) == original


def test_offset_timestamps_are_converted_not_truncated():
    parsed = parse_utc("2026-06-01T19:00:00+10:00")
    assert parsed == datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)


def test_z_suffix_is_accepted():
    assert parse_utc("2026-06-01T09:00:00Z") == datetime(
        2026, 6, 1, 9, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("value", [None, "", "not a timestamp", "  "])
def test_unreadable_timestamps_return_none(value):
    assert parse_utc(value) is None


def test_stored_form_has_no_microseconds():
    """Fixed-width strings are what make SQL range filters chronological."""
    stamp = iso_utc(datetime(2026, 6, 1, 9, 0, 0, 123456, tzinfo=timezone.utc))
    assert stamp == "2026-06-01T09:00:00+00:00"


def test_stored_strings_sort_chronologically():
    earlier = iso_utc(datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc))
    later = iso_utc(datetime(2026, 6, 1, 9, 0, 1, tzinfo=timezone.utc))
    assert earlier < later


def test_day_bounds_span_exactly_one_local_day():
    day = datetime(2026, 6, 15, 13, 30).astimezone()
    assert local_day_end(day) - local_day_start(day) == timedelta(days=1)


def test_day_start_is_local_midnight():
    day = datetime(2026, 6, 15, 13, 30).astimezone()
    local_midnight = to_local(local_day_start(day))
    assert (local_midnight.hour, local_midnight.minute) == (0, 0)


@pytest.mark.parametrize("text,expected", [
    ("2026-06-15", (2026, 6, 15)),
    ("15/06/2026", (2026, 6, 15)),
    ("15-06-2026", (2026, 6, 15)),
    ("2026/06/15", (2026, 6, 15)),
])
def test_parse_date_input_accepts_common_forms(text, expected):
    parsed = parse_date_input(text)
    assert (parsed.year, parsed.month, parsed.day) == expected


@pytest.mark.parametrize("text", ["", "   ", "tomorrow", "2026-13-45", "15 June"])
def test_parse_date_input_rejects_nonsense(text):
    assert parse_date_input(text) is None


def test_parse_datetime_input_treats_input_as_local():
    parsed = parse_datetime_input("2026-06-15 09:30")
    expected = to_utc(datetime(2026, 6, 15, 9, 30).astimezone())
    assert parsed == expected


@pytest.mark.parametrize("text", ["", "9:30", "2026-06-15 25:00", "rubbish"])
def test_parse_datetime_input_rejects_nonsense(text):
    assert parse_datetime_input(text) is None


def test_display_helpers_tolerate_missing_values():
    assert fmt_date(None) == ""
    assert fmt_date("") == ""


def test_now_utc_is_aware():
    assert now_utc().tzinfo is not None
