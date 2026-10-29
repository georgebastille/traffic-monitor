from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from traffic_monitor.analytics import compute_bucket_ema_baseline, compute_bucket_profile
from traffic_monitor.monitor import TrafficSample
from traffic_monitor.settings import Settings
from traffic_monitor.status import assess, resolve_target_arrival

ZONE = ZoneInfo("Europe/London")


def _weekday_history() -> list[TrafficSample]:
    # Mon 5 - Fri 9 Oct 2026, every 5 mins 07:00-08:25: 12 mins before 07:45, 18 mins from 07:45.
    samples = []
    for day in range(5, 10):
        for step in range(18):
            moment = datetime(2026, 10, day, 7, 0, tzinfo=ZONE) + timedelta(minutes=5 * step)
            duration = 12.0 + day - 5 if moment.hour == 7 and moment.minute < 45 else 18.0
            samples.append(TrafficSample(moment, moment, "O", "D", 10.0, duration))
    return samples


def test_assess_rates_now_and_departure_against_typical() -> None:
    history = _weekday_history()
    now = datetime(2026, 10, 12, 7, 30, tzinfo=ZONE)  # Monday
    current = TrafficSample(now, now, "O", "D", 10.0, 20.0)

    result = assess(history, current, now=now, arrival_time=time(8, 20), zone=ZONE, settings=Settings())

    assert result.typical_mins == pytest.approx(15.0)  # P75 of 12..16 at 07:30
    assert result.level == "red"  # +5 mins
    assert result.target_arrival == datetime(2026, 10, 12, 8, 20, tzinfo=ZONE)
    assert result.departure_typical_mins == pytest.approx(18.0)
    assert result.predicted_mins == pytest.approx(23.0)
    assert result.leave_by == datetime(2026, 10, 12, 7, 57, tzinfo=ZONE)


def test_resolve_target_arrival_skips_to_next_weekday() -> None:
    friday_evening = datetime(2026, 10, 9, 18, 0, tzinfo=ZONE)
    assert resolve_target_arrival(friday_evening, time(8, 20), ZONE) == datetime(2026, 10, 12, 8, 20, tzinfo=ZONE)


def test_bucket_profile_typical_matches_single_bucket_baseline() -> None:
    history = _weekday_history()
    profile = compute_bucket_profile(history, day=datetime(2026, 10, 12).date())

    for minute, stats in profile.items():
        target = datetime(2026, 10, 12, tzinfo=ZONE) + timedelta(minutes=minute)
        assert stats.typical == compute_bucket_ema_baseline(history, target_departure=target)
    assert profile[7 * 60 + 30].low == 12.0
    assert profile[7 * 60 + 30].high == 16.0
