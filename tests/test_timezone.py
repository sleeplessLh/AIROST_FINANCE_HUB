"""Timezone utility unit tests.
Run: python -m pytest tests/test_timezone.py -v
These tests do not use the database or the Flask app.
"""

from datetime import datetime, timedelta, timezone

from utils.timezone import MALAYSIA_TZ, format_malaysia_time, to_malaysia_time


def test_naive_utc_is_converted_to_malaysia_time():
    # 数据库读出的时间：没有时区信息，代表 UTC
    utc_time = datetime(2026, 11, 16, 2, 44)
    assert format_malaysia_time(utc_time) == "16 Nov 2026, 10:44"


def test_conversion_crosses_midnight_into_next_day():
    # UTC 晚上 20:30 = 马来西亚隔天 04:30，日期必须跟着改变
    utc_time = datetime(2026, 11, 16, 20, 30)
    assert format_malaysia_time(utc_time, "%Y-%m-%d %H:%M") == "2026-11-17 04:30"


def test_conversion_crosses_year_end():
    # 年底最后一天：UTC 12/31 18:00 = 马来西亚 1/1 02:00（新年）
    utc_time = datetime(2026, 12, 31, 18, 0)
    assert format_malaysia_time(utc_time, "%Y-%m-%d %H:%M") == "2027-01-01 02:00"


def test_aware_datetime_is_not_shifted_twice():
    # 已经带有时区的时间（例如已经是 UTC+8），不能再被加 8 小时
    already_malaysia = datetime(2026, 11, 16, 10, 44, tzinfo=MALAYSIA_TZ)
    assert format_malaysia_time(already_malaysia) == "16 Nov 2026, 10:44"


def test_result_has_plus_eight_offset():
    local_time = to_malaysia_time(datetime(2026, 11, 16, 2, 44))
    assert local_time.utcoffset() == timedelta(hours=8)


def test_none_returns_empty_string():
    # 例如尚未付款的 Claim，paid_at 是 None
    assert format_malaysia_time(None) == ""
    assert to_malaysia_time(None) is None


def test_custom_format_with_seconds():
    # Audit Logs 页面使用的格式
    utc_time = datetime(2026, 11, 16, 2, 44, 5)
    assert format_malaysia_time(utc_time, "%Y-%m-%d %H:%M:%S") == "2026-11-16 10:44:05"
