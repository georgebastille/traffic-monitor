from __future__ import annotations

import json
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from zoneinfo import ZoneInfo

from traffic_monitor.monitor import TrafficSample


def load_samples(path: Path | str, *, tzinfo: ZoneInfo | None = None) -> list[TrafficSample]:
    source = Path(path)
    if not source.exists():
        return []
    samples: list[TrafficSample] = []
    fallback_tz = tzinfo or timezone.utc
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            query = payload.get("query_time")
            departure = payload.get("departure_time")
            origin = payload.get("origin")
            destination = payload.get("destination")
            clear_duration = payload.get("clear_duration_mins")
            traffic_duration = payload.get("traffic_duration_mins")
            if not all([query, departure, origin, destination, traffic_duration]):
                continue
            try:
                query_dt = datetime.fromisoformat(str(query))
                if query_dt.tzinfo is None:
                    query_dt = query_dt.replace(tzinfo=fallback_tz)
                departure_dt = datetime.fromisoformat(str(departure))
                if departure_dt.tzinfo is None:
                    departure_dt = departure_dt.replace(tzinfo=fallback_tz)
                samples.append(
                    TrafficSample(
                        query_time=query_dt,
                        departure_time=departure_dt,
                        origin=str(origin),
                        destination=str(destination),
                        clear_duration_mins=float(clear_duration) if clear_duration is not None else 0.0,
                        traffic_duration_mins=float(traffic_duration),
                    )
                )
            except (ValueError, TypeError):
                continue
    return sorted(samples, key=lambda sample: sample.query_time)


def filter_recent_weekday_samples(
    samples: Sequence[TrafficSample],
    *,
    reference: datetime,
    weeks: int = 4,
) -> list[TrafficSample]:
    cutoff = reference - timedelta(weeks=weeks)
    return [
        sample
        for sample in samples
        if sample.query_time >= cutoff and sample.departure_time.weekday() < 5
    ]


def compute_baseline_duration(samples: Sequence[TrafficSample]) -> float | None:
    durations = [sample.traffic_duration_mins for sample in samples]
    if not durations:
        return None
    return statistics.median(durations)


def compute_bucket_ema_baseline(
    samples: Sequence[TrafficSample],
    *,
    target_departure: datetime,
    max_weekdays: int = 5,
    bucket_minutes: int = 5,
    ema_span: int = 5,
) -> float | None:
    """
    Compute an exponential moving average baseline for the target departure bucket.

    The EMA is calculated from the most recent ``max_weekdays`` weekday samples
    that fall into the same time-of-day bucket as ``target_departure``. Weekends
    and the target day itself are excluded so the baseline reflects historical
    behaviour only.
    """
    if max_weekdays <= 0:
        raise ValueError("max_weekdays must be positive")
    if bucket_minutes <= 0:
        raise ValueError("bucket_minutes must be positive")
    if ema_span <= 0:
        raise ValueError("ema_span must be positive")

    bucket = _bucket_index(target_departure, bucket_minutes)
    target_date = target_departure.date()
    by_day: dict[date, list[float]] = {}
    for sample in samples:
        departure = sample.departure_time
        if departure.weekday() >= 5:
            continue
        sample_date = departure.date()
        if sample_date >= target_date:
            continue
        if _bucket_index(departure, bucket_minutes) != bucket:
            continue
        by_day.setdefault(sample_date, []).append(sample.traffic_duration_mins)

    if not by_day:
        return None
    ordered_days = sorted(by_day.keys())
    recent_days = ordered_days[-max_weekdays:]
    values = [statistics.fmean(by_day[day]) for day in recent_days]
    return _compute_ema(values, span=ema_span)


def minutes_since_midnight(moment: datetime) -> float:
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return (moment - midnight).total_seconds() / 60.0


def compute_time_of_day_stats(
    samples: Sequence[TrafficSample],
    *,
    target_minutes: float,
    tolerance_minutes: float = 10.0,
) -> tuple[float, float] | None:
    values = [
        sample.traffic_duration_mins
        for sample in samples
        if abs(minutes_since_midnight(sample.departure_time) - target_minutes) <= tolerance_minutes
    ]
    if len(values) < 2:
        return None
    mean = statistics.fmean(values)
    stdev = statistics.pstdev(values)
    if stdev == 0.0:
        return None
    return mean, stdev


def prune_jsonl_history(
    path: Path | str,
    *,
    cutoff: datetime,
) -> int:
    """
    Trim JSONL records older than ``cutoff``. Returns the number of removed rows.
    """
    source = Path(path)
    if not source.exists():
        return 0
    removed = 0
    kept_lines: list[str] = []
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                kept_lines.append(stripped)
                continue
            query_time = payload.get("query_time")
            if not query_time:
                kept_lines.append(stripped)
                continue
            try:
                moment = datetime.fromisoformat(str(query_time))
            except ValueError:
                kept_lines.append(stripped)
                continue
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=cutoff.tzinfo)
            if moment >= cutoff:
                kept_lines.append(json.dumps(payload))
            else:
                removed += 1
    if removed > 0:
        with source.open("w", encoding="utf-8") as handle:
            for line in kept_lines:
                handle.write(f"{line}\n")
    return removed


def _bucket_index(moment: datetime, bucket_minutes: int) -> int:
    return int(minutes_since_midnight(moment) // bucket_minutes)


def _compute_ema(values: Sequence[float], *, span: int) -> float | None:
    if not values:
        return None
    if span <= 1 or len(values) == 1:
        return float(values[-1])
    alpha = 2.0 / (span + 1.0)
    ema = float(values[0])
    for value in values[1:]:
        ema = alpha * float(value) + (1.0 - alpha) * ema
    return ema
