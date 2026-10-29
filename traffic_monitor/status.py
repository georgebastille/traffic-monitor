from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Sequence
from zoneinfo import ZoneInfo

from traffic_monitor.analytics import (
    compute_bucket_baseline,
    filter_recent_weekday_samples,
    predict_departure_duration,
)
from traffic_monitor.monitor import TrafficSample
from traffic_monitor.notifications import traffic_level
from traffic_monitor.settings import Settings


@dataclass(frozen=True)
class Assessment:
    """
    How traffic compares with usual right now, and when to leave for the next arrival.

    ``typical_mins`` is the median (what change alerts compare against);
    ``departure_typical_mins`` is the cautious P75 the leave-by time is built on.
    """

    current_mins: float
    typical_mins: float
    level: str
    target_arrival: datetime
    leave_by: datetime
    predicted_mins: float
    departure_typical_mins: float
    departure_level: str


def assess(
    history: Sequence[TrafficSample],
    current: TrafficSample,
    *,
    now: datetime,
    arrival_time: time,
    zone: ZoneInfo,
    settings: Settings,
) -> Assessment:
    """Shared by the 5-minute monitor (for notifications) and the API (for the app)."""
    recent = filter_recent_weekday_samples(history, reference=now)

    def baseline(target: datetime, percentile: int) -> float | None:
        return compute_bucket_baseline(
            recent, target_departure=target, percentile=percentile, window_minutes=settings.baseline_window_minutes
        )

    current_mins = current.traffic_duration_mins
    typical = baseline(current.departure_time, settings.alert_percentile) or current_mins
    departure_now = baseline(current.departure_time, settings.departure_percentile) or current_mins
    target_arrival = resolve_target_arrival(now, arrival_time, zone)
    predicted, departure_typical = predict_departure_duration(
        recent,
        current_duration_mins=current_mins,
        current_baseline_mins=departure_now,
        target_arrival=target_arrival,
        percentile=settings.departure_percentile,
        window_minutes=settings.baseline_window_minutes,
    )
    thresholds = {"orange_mins": settings.orange_mins, "red_mins": settings.red_mins}
    return Assessment(
        current_mins=current.traffic_duration_mins,
        typical_mins=typical,
        level=traffic_level(current.traffic_duration_mins - typical, **thresholds),
        target_arrival=target_arrival,
        leave_by=target_arrival - timedelta(minutes=predicted),
        predicted_mins=predicted,
        departure_typical_mins=departure_typical,
        departure_level=traffic_level(predicted - departure_typical, **thresholds),
    )


def resolve_target_arrival(now: datetime, arrival_time: time, zone: ZoneInfo) -> datetime:
    """Today's arrival time, or the next weekday's once it has passed."""
    candidate = datetime.combine(now.date(), arrival_time, tzinfo=zone)
    if now > candidate:
        candidate = datetime.combine(_next_weekday(now.date() + timedelta(days=1)), arrival_time, tzinfo=zone)
    return candidate


def _next_weekday(start: date) -> date:
    current = start
    while current.weekday() >= 5:
        current += timedelta(days=1)
    return current
