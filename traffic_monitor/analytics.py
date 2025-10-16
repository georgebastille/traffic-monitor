from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta, timezone
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
