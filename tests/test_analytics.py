from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from traffic_monitor.analytics import (
    compute_bucket_ema_baseline,
    filter_recent_weekday_samples,
    load_samples,
    predict_departure_duration,
    prune_jsonl_history,
)
from traffic_monitor.monitor import TrafficSample


def make_sample(
    *,
    query_time: datetime,
    departure_time: datetime,
    duration: float,
) -> TrafficSample:
    return TrafficSample(
        query_time=query_time,
        departure_time=departure_time,
        origin="Origin",
        destination="Destination",
        clear_duration_mins=duration - 1.0,
        traffic_duration_mins=duration,
    )


def test_load_samples_returns_sorted(tmp_path: Path) -> None:
    path = tmp_path / "series.jsonl"
    newer = {
        "query_time": "2024-10-11T07:55:00+00:00",
        "departure_time": "2024-10-11T08:00:00+00:00",
        "origin": "Origin",
        "destination": "Destination",
        "clear_duration_mins": 15.0,
        "traffic_duration_mins": 18.0,
    }
    older = {
        "query_time": "2024-10-10T07:55:00+00:00",
        "departure_time": "2024-10-10T08:00:00+00:00",
        "origin": "Origin",
        "destination": "Destination",
        "clear_duration_mins": 14.0,
        "traffic_duration_mins": 17.0,
    }
    path.write_text("\n".join([json.dumps(newer), json.dumps(older)]) + "\n", encoding="utf-8")

    samples = load_samples(path, tzinfo=timezone.utc)

    assert [sample.query_time for sample in samples] == [
        datetime.fromisoformat(older["query_time"]),
        datetime.fromisoformat(newer["query_time"]),
    ]


def test_load_samples_skips_incomplete_records(tmp_path: Path) -> None:
    path = tmp_path / "series.jsonl"
    missing_departure = {
        "query_time": "2024-10-10T07:55:00+00:00",
        "origin": "Origin",
        "destination": "Destination",
        "traffic_duration_mins": 18.0,
    }
    malformed = "{bad json"
    valid = {
        "query_time": "2024-10-11T07:55:00+00:00",
        "departure_time": "2024-10-11T08:00:00+00:00",
        "origin": "Origin",
        "destination": "Destination",
        "clear_duration_mins": 15.0,
        "traffic_duration_mins": 18.0,
    }
    path.write_text("\n".join([json.dumps(missing_departure), malformed, json.dumps(valid)]) + "\n", encoding="utf-8")

    samples = load_samples(path, tzinfo=timezone.utc)

    assert [sample.query_time.isoformat() for sample in samples] == ["2024-10-11T07:55:00+00:00"]


def test_load_samples_normalises_naive_datetimes(tmp_path: Path) -> None:
    path = tmp_path / "series.jsonl"
    naive = {
        "query_time": "2024-10-10T07:55:00",
        "departure_time": "2024-10-10T08:00:00",
        "origin": "Origin",
        "destination": "Destination",
        "traffic_duration_mins": 18.0,
    }
    aware = {
        "query_time": "2024-10-11T07:55:00+00:00",
        "departure_time": "2024-10-11T08:00:00+00:00",
        "origin": "Origin",
        "destination": "Destination",
        "traffic_duration_mins": 19.0,
    }
    path.write_text("\n".join([json.dumps(naive), json.dumps(aware)]) + "\n", encoding="utf-8")

    samples = load_samples(path, tzinfo=timezone.utc)

    assert len(samples) == 2
    assert samples[0].query_time.tzinfo is not None
    assert samples[1].query_time.tzinfo is not None


def test_filter_recent_weekday_samples_limits_range() -> None:
    now = datetime(2024, 10, 10, 7, 0, tzinfo=timezone.utc)
    recent = make_sample(
        query_time=now - timedelta(days=7),
        departure_time=now - timedelta(days=7),
        duration=18.0,
    )
    old = make_sample(
        query_time=now - timedelta(days=35),
        departure_time=now - timedelta(days=35),
        duration=19.0,
    )
    saturday = make_sample(
        query_time=datetime(2024, 10, 5, 7, 0, tzinfo=timezone.utc),
        departure_time=datetime(2024, 10, 5, 8, 0, tzinfo=timezone.utc),
        duration=21.0,
    )

    filtered = filter_recent_weekday_samples([recent, old, saturday], reference=now)

    assert filtered == [recent]


def test_compute_bucket_ema_baseline_uses_last_five_weekdays() -> None:
    base = datetime(2024, 10, 7, 8, 0, tzinfo=timezone.utc)  # Monday
    durations = [20.0, 21.0, 19.0, 18.0, 22.0, 25.0]  # Mon -> next Mon
    day_offsets = [0, 1, 2, 3, 4, 7]  # skip the weekend
    samples = [
        make_sample(
            query_time=base + timedelta(days=offset),
            departure_time=base + timedelta(days=offset),
            duration=duration,
        )
        for offset, duration in zip(day_offsets, durations)
    ]
    target_departure = base + timedelta(days=8)  # Tuesday following the last sample

    baseline = compute_bucket_ema_baseline(
        samples,
        target_departure=target_departure,
        max_weekdays=5,
        bucket_minutes=5,
        ema_span=5,
        baseline_percentile=50,
    )

    assert baseline is not None
    # Manually calculated EMA over last 5 weekdays (Tue->Mon with alpha=1/3)
    assert baseline == pytest.approx(21.91, abs=0.01)

    # Default P75: nearest-rank over the same five days [18, 19, 21, 22, 25].
    assert compute_bucket_ema_baseline(samples, target_departure=target_departure) == pytest.approx(22.0)


def test_compute_bucket_ema_baseline_averages_same_day_bucket() -> None:
    base = datetime(2024, 10, 7, 8, 0, tzinfo=timezone.utc)
    same_day_samples = [
        make_sample(
            query_time=base,
            departure_time=base,
            duration=20.0,
        ),
        make_sample(
            query_time=base + timedelta(minutes=5),
            departure_time=base + timedelta(minutes=5),
            duration=22.0,
        ),
    ]
    later_day = make_sample(
        query_time=base + timedelta(days=1),
        departure_time=base + timedelta(days=1),
        duration=24.0,
    )
    samples = same_day_samples + [later_day]

    baseline = compute_bucket_ema_baseline(
        samples,
        target_departure=base + timedelta(days=2),
        baseline_percentile=50,
    )

    # First day values should average to 21, then EMA towards 24.
    assert baseline == pytest.approx(22.0, abs=0.01)


def test_prune_jsonl_history_removes_records_before_cutoff(tmp_path: Path) -> None:
    path = tmp_path / "series.jsonl"
    older = {
        "query_time": "2024-09-20T07:55:00+00:00",
        "departure_time": "2024-09-20T08:00:00+00:00",
        "origin": "Origin",
        "destination": "Destination",
        "traffic_duration_mins": 18.0,
    }
    newer = {
        "query_time": "2024-10-10T07:55:00+00:00",
        "departure_time": "2024-10-10T08:00:00+00:00",
        "origin": "Origin",
        "destination": "Destination",
        "traffic_duration_mins": 19.0,
    }
    path.write_text("\n".join([json.dumps(older), json.dumps(newer)]) + "\n", encoding="utf-8")

    cutoff = datetime(2024, 10, 1, tzinfo=timezone.utc)
    removed = prune_jsonl_history(path, cutoff=cutoff)

    assert removed == 1
    remaining = load_samples(path, tzinfo=timezone.utc)
    assert len(remaining) == 1
    assert remaining[0].traffic_duration_mins == pytest.approx(19.0)


def test_predict_departure_duration_shifts_by_baseline_profile() -> None:
    # History every 5 mins: 12 mins before 07:45, 18 mins from 07:45, on the previous weekdays.
    samples = []
    for day in range(5, 10):  # Mon 5 Oct -> Fri 9 Oct 2026
        for step in range(18):
            moment = datetime(2026, 10, day, 7, 0, tzinfo=timezone.utc) + timedelta(minutes=5 * step)
            duration = 12.0 if moment.minute < 45 and moment.hour == 7 else 18.0
            samples.append(make_sample(query_time=moment, departure_time=moment, duration=duration))

    predicted, departure_baseline = predict_departure_duration(
        samples,
        current_duration_mins=13.0,  # measured at 07:30, a minute slower than usual
        current_baseline_mins=12.0,
        target_arrival=datetime(2026, 10, 12, 8, 20, tzinfo=timezone.utc),
    )

    assert departure_baseline == pytest.approx(18.0)
    assert predicted == pytest.approx(19.0)


def test_predict_departure_duration_falls_back_without_history() -> None:
    predicted, departure_baseline = predict_departure_duration(
        [],
        current_duration_mins=13.0,
        current_baseline_mins=12.0,
        target_arrival=datetime(2026, 10, 12, 8, 20, tzinfo=timezone.utc),
    )

    assert (predicted, departure_baseline) == (13.0, 12.0)
