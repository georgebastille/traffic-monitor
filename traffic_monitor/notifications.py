from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Optional

from traffic_monitor.analytics import minutes_since_midnight
from traffic_monitor.state import NotificationState

LEVELS = ("green", "orange", "red")
LEVEL_EMOJI = {"green": "\U0001f7e2", "orange": "\U0001f7e0", "red": "\U0001f534"}
LEVEL_PRIORITY = {"green": 3, "orange": 3, "red": 4}


@dataclass(frozen=True)
class Notification:
    """A traffic-light notification; ``title`` is kept short so a watch shows the essentials."""

    level: str
    title: str
    body: str

    @property
    def emoji(self) -> str:
        return LEVEL_EMOJI[self.level]


@dataclass(frozen=True)
class DepartureDecision:
    notification: Notification
    departure_minutes: float


@dataclass(frozen=True)
class PatternAlertDecision:
    notification: Optional[Notification]
    state_changed: bool


def traffic_level(delay_mins: float, *, orange_mins: float = 2.0, red_mins: float = 5.0) -> str:
    if delay_mins >= red_mins:
        return "red"
    if delay_mins >= orange_mins:
        return "orange"
    return "green"


def evaluate_departure_notification(
    *,
    now: datetime,
    arrival_time: time,
    target_arrival: datetime,
    current_duration_mins: float,
    baseline_duration_mins: float,
    lead_time: timedelta,
    state: NotificationState,
    orange_mins: float = 2.0,
    red_mins: float = 5.0,
    resend_earlier_mins: float = 3.0,
) -> Optional[DepartureDecision]:
    """
    Send a leave-by notification from ``lead_time`` before the recommended departure.

    After the first one each day, only re-send when the level worsens or the leave
    time moves at least ``resend_earlier_mins`` earlier.
    """
    if target_arrival.date().weekday() >= 5:
        return None
    recommended_departure = target_arrival - timedelta(minutes=current_duration_mins)
    if recommended_departure <= now:
        return None
    notify_at = recommended_departure - lead_time
    if now < notify_at:
        return None
    delay = current_duration_mins - baseline_duration_mins
    level = traffic_level(delay, orange_mins=orange_mins, red_mins=red_mins)
    recommended_minutes = minutes_since_midnight(recommended_departure)
    if state.departure_date == target_arrival.date() and state.departure_minutes is not None:
        worse_level = LEVELS.index(level) > LEVELS.index(state.departure_level or "green")
        much_earlier = recommended_minutes <= state.departure_minutes - resend_earlier_mins
        if not (worse_level or much_earlier):
            return None

    leave = recommended_departure.strftime("%H:%M")
    arrive = arrival_time.strftime("%H:%M")
    trip = f"{current_duration_mins:.0f}m"
    typical = f"{baseline_duration_mins:.0f}"
    if level == "green":
        title = f"Leave {leave} · normal ({trip})"
        body = f"Usual route is fine: {current_duration_mins:.0f} min (typical {typical}) to arrive {arrive}."
    elif level == "orange":
        title = f"Leave {leave} · +{delay:.0f}m ({trip})"
        body = f"A bit slower than usual (typical {typical}). Usual route OK, allow extra time to arrive {arrive}."
    else:
        title = f"Leave {leave} · +{delay:.0f}m ({trip}) · check Waze"
        body = (
            f"Significant delays: {current_duration_mins:.0f} min vs typical {typical}. "
            f"Check Waze for alternatives to arrive {arrive}."
        )
    return DepartureDecision(
        notification=Notification(level=level, title=title, body=body),
        departure_minutes=recommended_minutes,
    )


def evaluate_pattern_alert(
    *,
    sample_time: datetime,
    current_duration_mins: float,
    baseline_duration_mins: float | None,
    state: NotificationState,
    deadband_minutes: float = 2.0,
    integral_threshold: float = 90.0,
    decay_minutes: float = 120.0,
    cooldown_minutes: float = 120.0,
    sample_interval_minutes: float = 5.0,
    max_sample_gap_minutes: float = 15.0,
    orange_mins: float = 2.0,
    red_mins: float = 5.0,
) -> PatternAlertDecision:
    """
    Integrate deviations between the current duration and baseline to detect sustained anomalies.

    Fires when the time-weighted deviation integral exceeds ``integral_threshold``
    (default 90 minute-minutes ≈ 30 min of +5 min traffic). After firing, a
    ``cooldown_minutes`` window must pass before re-alerting, allowing genuine
    second events later in the day to be reported.

    The integral is reset to zero at day boundaries so morning events never
    contaminate the next day's accumulation.
    """
    if baseline_duration_mins is None or baseline_duration_mins <= 0:
        return PatternAlertDecision(notification=None, state_changed=False)
    if sample_time.weekday() >= 5:
        return PatternAlertDecision(notification=None, state_changed=False)

    state_changed = False
    now = sample_time

    # Reset integral at day boundaries so each school-run day starts clean.
    if state.anomaly_last_timestamp is not None and state.anomaly_last_timestamp.date() < sample_time.date():
        state.anomaly_integral_high = 0.0
        state.anomaly_integral_low = 0.0
        state_changed = True

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
        return PatternAlertDecision(notification=None, state_changed=True)

    state.anomaly_last_timestamp = sample_time

    if direction == "longer":
        integral_value = state.anomaly_integral_high
    else:
        integral_value = state.anomaly_integral_low

    if integral_value < integral_threshold:
        return PatternAlertDecision(notification=None, state_changed=state_changed)

    # Cooldown: require ``cooldown_minutes`` to elapse since last alert so that
    # a genuine second event later in the day can still fire.
    if state.pattern_alert_timestamp is not None:
        mins_since_alert = (sample_time - state.pattern_alert_timestamp).total_seconds() / 60.0
        if mins_since_alert < cooldown_minutes:
            return PatternAlertDecision(notification=None, state_changed=state_changed)

    now_text = f"{current_duration_mins:.0f}m vs {baseline_duration_mins:.0f}m usual"
    if direction == "longer":
        level = traffic_level(deviation, orange_mins=orange_mins, red_mins=red_mins)
        title = f"Traffic +{deviation:.0f}m now ({now_text})"
        if level == "red":
            body = "Significant delays on the usual route. Check Waze before setting off."
        else:
            body = "A bit slower than usual on the usual route."
    else:
        level = "green"
        title = f"Traffic eased ({now_text})"
        body = "Quieter than usual on the usual route."
    notification = Notification(level=level, title=title, body=body)
    state.pattern_alert_date = now.date()
    state.pattern_alert_timestamp = now
    state.anomaly_integral_high = 0.0
    state.anomaly_integral_low = 0.0
    state_changed = True
    return PatternAlertDecision(notification=notification, state_changed=state_changed)


def _decay_factor(delta_minutes: float, decay_minutes: float) -> float:
    if decay_minutes <= 0:
        return 0.0
    return math.exp(-delta_minutes / decay_minutes)
