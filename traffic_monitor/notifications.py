from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Optional

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
    ema_minutes: float | None,
    stats: tuple[float, float] | None,
    state: NotificationState,
) -> Optional[str]:
    if ema_minutes is None or stats is None:
        return None
    if now.weekday() >= 5:
        return None
    mean, stdev = stats
    if stdev <= 0.05:
        return None
    deviation = ema_minutes - mean
    threshold = 2 * stdev
    if abs(deviation) < threshold:
        return None
    if state.pattern_alert_date == now.date():
        return None
    direction = "longer" if deviation > 0 else "shorter"
    message = (
        f"Traffic pattern changed: EMA {abs(deviation):.1f} mins {direction} than normal "
        f"(avg {mean:.1f} mins, σ={stdev:.1f})."
    )
    return message
