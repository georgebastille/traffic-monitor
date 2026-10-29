from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from traffic_monitor.analytics import (
    compute_bucket_baseline,
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


def test_compute_bucket_baseline_uses_last_five_weekdays() -> None:
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

    # Nearest rank over the last five weekdays [18, 19, 21, 22, 25]; the first Monday drops out.
    assert compute_bucket_baseline(samples, target_departure=target_departure) == pytest.approx(22.0)
    assert compute_bucket_baseline(samples, target_departure=target_departure, percentile=50) == pytest.approx(21.0)


def test_compute_bucket_baseline_window_pools_neighbouring_buckets() -> None:
    day = datetime(2024, 10, 7, tzinfo=timezone.utc)  # Monday
    samples = [
        make_sample(query_time=day.replace(hour=7, minute=m), departure_time=day.replace(hour=7, minute=m), duration=d)
        for m, d in [(40, 10.0), (45, 11.0), (50, 30.0), (55, 13.0), (59, 14.0)]
    ]
    target = day.replace(hour=7, minute=50) + timedelta(days=1)

    # Without a window only the 07:50 spike counts; ±10 minutes pools 07:40-08:00 and the median ignores it.
    assert compute_bucket_baseline(samples, target_departure=target, percentile=50) == pytest.approx(30.0)
    assert compute_bucket_baseline(samples, target_departure=target, percentile=50, window_minutes=10) == pytest.approx(
        13.0
    )


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
