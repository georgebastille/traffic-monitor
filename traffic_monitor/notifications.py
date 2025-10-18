from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Optional

from traffic_monitor.analytics import minutes_since_midnight
from traffic_monitor.state import NotificationState


@dataclass(frozen=True)
class DepartureDecision:
    message: str
    departure_minutes: float


@dataclass(frozen=True)
class PatternAlertDecision:
    message: Optional[str]
    state_changed: bool


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
    sample_time: datetime,
    current_duration_mins: float,
    baseline_duration_mins: float | None,
    state: NotificationState,
    deadband_minutes: float = 2.0,
    integral_threshold: float = 180.0,
    decay_minutes: float = 120.0,
    sample_interval_minutes: float = 5.0,
    max_sample_gap_minutes: float = 15.0,
) -> PatternAlertDecision:
    """
    Integrate deviations between the current duration and baseline to detect sustained anomalies.
    """
    if baseline_duration_mins is None or baseline_duration_mins <= 0:
        return PatternAlertDecision(message=None, state_changed=False)
    if sample_time.weekday() >= 5:
        return PatternAlertDecision(message=None, state_changed=False)

    state_changed = False
    now = sample_time

    # Determine elapsed minutes since the last observation we incorporated.
    if state.anomaly_last_timestamp is not None:
        delta_minutes = (sample_time - state.anomaly_last_timestamp).total_seconds() / 60.0
    else:
        delta_minutes = sample_interval_minutes
    if delta_minutes <= 0:
        delta_minutes = sample_interval_minutes
    delta_minutes = min(delta_minutes, max_sample_gap_minutes)

    decay_factor = _decay_factor(delta_minutes, decay_minutes)
    if decay_factor < 1.0:
        state.anomaly_integral_high *= decay_factor
        state.anomaly_integral_low *= decay_factor
        state_changed = True

    deviation = current_duration_mins - baseline_duration_mins
    contribution = 0.0
    direction: Optional[str] = None

    if deviation > deadband_minutes:
        contribution = (deviation - deadband_minutes) * delta_minutes
        state.anomaly_integral_high += contribution
        state.anomaly_integral_low = 0.0
        direction = "longer"
        state_changed = True
    elif deviation < -deadband_minutes:
        contribution = (abs(deviation) - deadband_minutes) * delta_minutes
        state.anomaly_integral_low += contribution
        state.anomaly_integral_high = 0.0
        direction = "shorter"
        state_changed = True
    else:
        # Within the comfort band; minor decay has already been applied.
        if state.anomaly_integral_high < 1e-3:
            state.anomaly_integral_high = 0.0
        if state.anomaly_integral_low < 1e-3:
            state.anomaly_integral_low = 0.0
        state.anomaly_last_timestamp = sample_time
        return PatternAlertDecision(message=None, state_changed=True)

    state.anomaly_last_timestamp = sample_time

    if direction == "longer":
        integral_value = state.anomaly_integral_high
    else:
        integral_value = state.anomaly_integral_low

    if integral_value < integral_threshold:
        return PatternAlertDecision(message=None, state_changed=state_changed)

    if state.pattern_alert_date == now.date():
        return PatternAlertDecision(message=None, state_changed=state_changed)

    message = (
        f"Traffic pattern changed: sustained {direction} travel times "
        f"by ~{abs(deviation):.1f} mins (baseline {baseline_duration_mins:.1f} mins)."
    )
    state.pattern_alert_date = now.date()
    state.anomaly_integral_high = 0.0
    state.anomaly_integral_low = 0.0
    state_changed = True
    return PatternAlertDecision(message=message, state_changed=state_changed)


def _decay_factor(delta_minutes: float, decay_minutes: float) -> float:
    if decay_minutes <= 0:
        return 0.0
    return math.exp(-delta_minutes / decay_minutes)
