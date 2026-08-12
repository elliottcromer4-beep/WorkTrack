import pytest

from src.models import (
    format_duration,
    format_duration_human,
    format_hm,
    format_hours_decimal,
)


@pytest.mark.parametrize("seconds,expected", [
    (0, "00:00"),
    (59, "00:59"),
    (60, "01:00"),
    (3599, "59:59"),
    (3600, "1:00:00"),
    (3661, "1:01:01"),
    (-5, "00:00"),
])
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected


@pytest.mark.parametrize("seconds,expected", [
    (0, "0s"),
    (45, "45s"),
    (1800, "30m"),          # not "30m 0s"
    (3600, "1h"),
    (4500, "1h 15m"),
    (-10, "0s"),
])
def test_format_duration_human(seconds, expected):
    assert format_duration_human(seconds) == expected


@pytest.mark.parametrize("seconds,expected", [
    (0, "0:00"),
    (1800, "0:30"),
    (3600, "1:00"),
    (26100, "7:15"),
    (29, "0:00"),           # rounds to the nearest minute
    (31, "0:01"),
])
def test_format_hm(seconds, expected):
    assert format_hm(seconds) == expected


@pytest.mark.parametrize("seconds,expected", [
    (0, "0.00"),
    (3600, "1.00"),
    (26100, "7.25"),
    (1800, "0.50"),
])
def test_format_hours_decimal(seconds, expected):
    assert format_hours_decimal(seconds) == expected


def test_hm_never_hides_the_hour():
    """A timesheet column must stay vertically comparable."""
    assert format_hm(3600).count(":") == 1
    assert format_hm(60).startswith("0:")
