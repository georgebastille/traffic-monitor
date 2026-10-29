from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass
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
    return [sample for sample in samples if sample.query_time >= cutoff and sample.departure_time.weekday() < 5]


def compute_bucket_ema_baseline(
    samples: Sequence[TrafficSample],
    *,
    target_departure: datetime,
    max_weekdays: int = 5,
    bucket_minutes: int = 5,
    ema_span: int = 3,
    baseline_percentile: int = 75,
) -> float | None:
    """
    Compute a baseline travel time for the target departure bucket.

    By default uses the P75 across the most recent ``max_weekdays`` weekday
    samples in the same time-of-day bucket, giving a conservative estimate
    that is exceeded only ~25% of the time.

    When ``baseline_percentile`` is 50 the original behaviour is restored:
    EMA of daily medians with span ``ema_span``.

    Weekends and the target day itself are always excluded.
    """
    if max_weekdays <= 0:
        raise ValueError("max_weekdays must be positive")
    if bucket_minutes <= 0:
        raise ValueError("bucket_minutes must be positive")
    if ema_span <= 0:
        raise ValueError("ema_span must be positive")
    if not (1 <= baseline_percentile <= 99):
        raise ValueError("baseline_percentile must be between 1 and 99")

    bucket = _bucket_index(target_departure, bucket_minutes)
    by_day = _group_weekday_buckets(samples, before=target_departure.date(), bucket_minutes=bucket_minutes).get(bucket)
    if not by_day:
        return None
    return _bucket_baseline(
        by_day, max_weekdays=max_weekdays, ema_span=ema_span, baseline_percentile=baseline_percentile
    )


@dataclass(frozen=True)
class BucketStats:
    typical: float
    low: float
    high: float


def compute_bucket_profile(
    samples: Sequence[TrafficSample],
    *,
    day: date,
    max_weekdays: int = 5,
    bucket_minutes: int = 5,
    ema_span: int = 3,
    baseline_percentile: int = 75,
) -> dict[int, BucketStats]:
    """
    Baseline for every time-of-day bucket of ``day``, keyed by bucket start minute.

    ``typical`` is exactly what :func:`compute_bucket_ema_baseline` returns for that
    bucket; ``low``/``high`` span the same recent weekdays' values.
    """
    profile: dict[int, BucketStats] = {}
    grouped = _group_weekday_buckets(samples, before=day, bucket_minutes=bucket_minutes)
    for bucket, by_day in grouped.items():
        recent_days = sorted(by_day)[-max_weekdays:]
        values = [value for recent in recent_days for value in by_day[recent]]
        typical = _bucket_baseline(
            by_day, max_weekdays=max_weekdays, ema_span=ema_span, baseline_percentile=baseline_percentile
        )
        if typical is not None:
            profile[bucket * bucket_minutes] = BucketStats(typical=typical, low=min(values), high=max(values))
    return profile


def _group_weekday_buckets(
    samples: Sequence[TrafficSample], *, before: date, bucket_minutes: int
) -> dict[int, dict[date, list[float]]]:
    grouped: dict[int, dict[date, list[float]]] = {}
    for sample in samples:
        departure = sample.departure_time
        if departure.weekday() >= 5 or departure.date() >= before:
            continue
        bucket = _bucket_index(departure, bucket_minutes)
        grouped.setdefault(bucket, {}).setdefault(departure.date(), []).append(sample.traffic_duration_mins)
    return grouped


def _bucket_baseline(
    by_day: dict[date, list[float]], *, max_weekdays: int, ema_span: int, baseline_percentile: int
) -> float | None:
    recent_days = sorted(by_day)[-max_weekdays:]
    if baseline_percentile == 50:
        return _compute_ema([statistics.fmean(by_day[day]) for day in recent_days], span=ema_span)
    all_values = sorted(v for day in recent_days for v in by_day[day])
    if len(all_values) == 1:
        return all_values[0]
    idx = int(math.ceil(baseline_percentile / 100 * len(all_values))) - 1
    return all_values[max(0, min(idx, len(all_values) - 1))]


def predict_departure_duration(
    samples: Sequence[TrafficSample],
    *,
    current_duration_mins: float,
    current_baseline_mins: float,
    target_arrival: datetime,
    baseline_percentile: int = 75,
    iterations: int = 2,
) -> tuple[float, float]:
    """
    Predict the trip duration at the recommended departure time.

    Shifts the current duration by how much the baseline changes between now and
    the departure bucket. Iterates because the departure time depends on the
    predicted duration. Returns ``(predicted_mins, departure_baseline_mins)``,
    falling back to the current values when the departure bucket has no history.
    """
    predicted = current_duration_mins
    departure_baseline = current_baseline_mins
    for _ in range(iterations):
        baseline = compute_bucket_ema_baseline(
            samples,
            target_departure=target_arrival - timedelta(minutes=predicted),
            baseline_percentile=baseline_percentile,
        )
        if baseline is None:
            return current_duration_mins, current_baseline_mins
        departure_baseline = baseline
        predicted = current_duration_mins + baseline - current_baseline_mins
    return predicted, departure_baseline


def minutes_since_midnight(moment: datetime) -> float:
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return (moment - midnight).total_seconds() / 60.0


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
