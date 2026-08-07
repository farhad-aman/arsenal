import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from dl import clock
from dl.format import DASH


@pytest.fixture
def tehran(monkeypatch):
    """Run the body with the machine sitting in Tehran (+03:30)."""
    monkeypatch.setenv("TZ", "Asia/Tehran")
    time.tzset()
    yield
    monkeypatch.delenv("TZ", raising=False)
    time.tzset()


def epoch_of(text: str) -> int:
    """A UTC wall-clock reading, as the integer dl would store."""
    return int(datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp())


def test_utc_noon_reads_as_tehran_half_past_three(tehran):
    noon = epoch_of("2026-08-07 12:00")
    assert clock.stamp(noon, now=noon) == "15:30"


def test_same_instant_reads_differently_in_another_zone(monkeypatch):
    noon = epoch_of("2026-08-07 12:00")
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    try:
        assert clock.stamp(noon, now=noon) == "12:00"
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


def test_earlier_day_carries_the_full_date_and_year(tehran):
    then = epoch_of("2026-08-06 12:00")
    now = epoch_of("2026-08-07 12:00")
    assert clock.stamp(then, now=now) == "2026-08-06 15:30"


def test_today_is_the_local_calendar_day_not_a_24_hour_window(tehran):
    """23:00 local yesterday is 5 hours ago, and still not today."""
    now = epoch_of("2026-08-07 00:30")  # 04:00 local on the 7th
    then = now - 5 * 3600  # 23:00 local on the 6th
    assert clock.stamp(then, now=now).startswith("2026-08-06")


def test_this_morning_is_today_even_though_utc_says_yesterday(tehran):
    """01:00 local on the 7th is 21:30 UTC on the 6th — local date decides."""
    then = epoch_of("2026-08-06 21:30")
    now = epoch_of("2026-08-07 05:00")
    assert clock.stamp(then, now=now) == "01:00"


def test_missing_timestamp_is_a_dash():
    assert clock.stamp(0) == DASH
    assert clock.stamp(None) == DASH
    assert clock.stamp("") == DASH


def test_unreadable_timestamp_is_a_dash_rather_than_a_crash():
    assert clock.stamp("not a time") == DASH
    assert clock.stamp(-1) == DASH


def test_absurd_timestamp_is_a_dash_rather_than_a_crash():
    assert clock.stamp(10**20) == DASH


def test_now_defaults_to_the_present(tehran):
    assert clock.stamp(int(time.time())) == time.strftime("%H:%M")


def test_cell_is_wide_enough_for_the_longest_stamp(tehran):
    then = epoch_of("2026-08-06 12:00")
    now = epoch_of("2026-08-07 12:00")
    assert len(clock.stamp(then, now=now)) == clock.CELL


def test_stamp_accepts_a_float_timestamp(tehran):
    noon = epoch_of("2026-08-07 12:00")
    assert clock.stamp(noon + 0.7, now=noon) == "15:30"


def test_dst_shift_still_lands_on_the_right_local_day(monkeypatch):
    """A zone that moved its clocks between then and now still dates correctly."""
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        before = epoch_of("2026-03-07 12:00")  # EST
        after = epoch_of("2026-03-20 12:00")  # EDT
        assert clock.stamp(before, now=after) == "2026-03-07 07:00"
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


def test_os_environ_tz_is_what_the_helper_reads(tehran):
    assert os.environ["TZ"] == "Asia/Tehran"
