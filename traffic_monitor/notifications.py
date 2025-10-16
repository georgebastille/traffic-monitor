from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Optional, Sequence

from traffic_monitor.analytics import minutes_since_midnight
from traffic_monitor.state import NotificationState


@dataclass(frozen=True)
class DepartureDecision:
    message: str
    departure_minutes: float


def evaluate_departure_notification(
    *,
    now: datetime,
    arrival_time: time,
    target_arrival: datetime,
    current_duration_mins: float,
    baseline_duration_mins: float,
    lead_time: timedelta,
    state: NotificationState,
) -> Optional[DepartureDecision]:
    if target_arrival.date().weekday() >= 5:
        return None
    recommended_departure = target_arrival - timedelta(minutes=current_duration_mins)
    if recommended_departure <= now:
        return None
    notify_at = recommended_departure - lead_time
    if now < notify_at:
        return None
    target_date = target_arrival.date()
    recommended_minutes = minutes_since_midnight(recommended_departure)
    last_minutes = state.departure_minutes if state.departure_date == target_date else None
    if last_minutes is not None and recommended_minutes >= last_minutes - 0.1:
        return None
    baseline_departure = target_arrival - timedelta(minutes=baseline_duration_mins)
    delta = current_duration_mins - baseline_duration_mins
    if delta >= 0:
        delta_text = f"+{delta:.1f} mins vs typical"
    else:
        delta_text = f"{abs(delta):.1f} mins faster than typical"
    message = (
        f"Leave by {recommended_departure.strftime('%H:%M')} to arrive for {arrival_time.strftime('%H:%M')} "
        f"(baseline {baseline_departure.strftime('%H:%M')}, {delta_text})."
    )
    return DepartureDecision(message=message, departure_minutes=recommended_minutes)


def evaluate_pattern_alert(
    *,
    now: datetime,
    series: Sequence[float],
    baseline: float | None,
    state: NotificationState,
) -> Optional[str]:
    if baseline is None:
        return None
    if now.weekday() >= 5:
        return None
    if len(series) < 3:
        return None
    recent = list(series)[-3:]
    if any(value <= 0 for value in recent):
        return None
    direction = None
    if all(value >= 1.25 * baseline for value in recent):
        direction = "longer"
        delta = min(value - baseline for value in recent)
    elif all(value <= 0.75 * baseline for value in recent):
        direction = "shorter"
        delta = baseline - max(value for value in recent)
    if direction is None:
        return None
    if state.pattern_alert_date == now.date():
        return None
    message = (
        f"Traffic pattern changed: last 3 samples are {direction} than normal "
        f"by at least {delta:.1f} mins (baseline {baseline:.1f} mins)."
    )
    return message
