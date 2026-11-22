"""Timezone utility unit tests.
Run: python -m pytest tests/test_timezone.py -v
These tests do not use the database or the Flask app.
"""

from datetime import datetime, timedelta, timezone

from utils.timezone import MALAYSIA_TZ, format_malaysia_time, to_malaysia_time


def test_naive_utc_is_converted_to_malaysia_time():
    utc_time = datetime(2026, 11, 16, 2, 44)
    assert format_malaysia_time(utc_time) == "16 Nov 2026, 10:44"


def test_conversion_crosses_midnight_into_next_day():
    utc_time = datetime(2026, 11, 16, 20, 30)
    assert format_malaysia_time(utc_time, "%Y-%m-%d %H:%M") == "2026-11-17 04:30"


def test_conversion_crosses_year_end():
    utc_time = datetime(2026, 12, 31, 18, 0)
    assert format_malaysia_time(utc_time, "%Y-%m-%d %H:%M") == "2027-01-01 02:00"


def test_aware_datetime_is_not_shifted_twice():
    already_malaysia = datetime(2026, 11, 16, 10, 44, tzinfo=MALAYSIA_TZ)
    assert format_malaysia_time(already_malaysia) == "16 Nov 2026, 10:44"


def test_result_has_plus_eight_offset():
    local_time = to_malaysia_time(datetime(2026, 11, 16, 2, 44))
    assert local_time.utcoffset() == timedelta(hours=8)


def test_none_returns_empty_string():
    assert format_malaysia_time(None) == ""
    assert to_malaysia_time(None) is None


def test_custom_format_with_seconds():
    utc_time = datetime(2026, 11, 16, 2, 44, 5)
    assert format_malaysia_time(utc_time, "%Y-%m-%d %H:%M:%S") == "2026-11-16 10:44:05"
