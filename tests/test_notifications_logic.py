from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from traffic_monitor.notifications import PatternAlertDecision, evaluate_departure_notification, evaluate_pattern_alert
from traffic_monitor.state import NotificationState

ZONE = ZoneInfo("Europe/London")
ARRIVAL_TIME = time(8, 20)


def test_departure_notification_triggers_at_lead_time() -> None:
    now = datetime(2024, 10, 10, 7, 5, tzinfo=ZONE)
    target_arrival = datetime(2024, 10, 10, ARRIVAL_TIME.hour, ARRIVAL_TIME.minute, tzinfo=ZONE)
    state = NotificationState()

    decision = evaluate_departure_notification(
        now=now,
        arrival_time=ARRIVAL_TIME,
        target_arrival=target_arrival,
        current_duration_mins=60.0,
        baseline_duration_mins=50.0,
        lead_time=timedelta(minutes=30),
        state=state,
    )

    assert decision is not None
    assert "Leave by" in decision.message
    assert decision.departure_minutes < 24 * 60


def test_departure_notification_skips_before_lead_time() -> None:
    now = datetime(2024, 10, 10, 6, 30, tzinfo=ZONE)
    target_arrival = datetime(2024, 10, 10, ARRIVAL_TIME.hour, ARRIVAL_TIME.minute, tzinfo=ZONE)
    state = NotificationState()

    decision = evaluate_departure_notification(
        now=now,
        arrival_time=ARRIVAL_TIME,
        target_arrival=target_arrival,
        current_duration_mins=60.0,
        baseline_duration_mins=50.0,
        lead_time=timedelta(minutes=30),
        state=state,
    )

    assert decision is None


def test_departure_notification_only_resends_for_earlier_times() -> None:
    target_arrival = datetime(2024, 10, 10, ARRIVAL_TIME.hour, ARRIVAL_TIME.minute, tzinfo=ZONE)
    state = NotificationState(departure_date=target_arrival.date(), departure_minutes=minutes(target_arrival, 60.0))

    # Later departure should not resend
    now = datetime(2024, 10, 10, 7, 30, tzinfo=ZONE)
    later = evaluate_departure_notification(
        now=now,
        arrival_time=ARRIVAL_TIME,
        target_arrival=target_arrival,
        current_duration_mins=40.0,
        baseline_duration_mins=50.0,
        lead_time=timedelta(minutes=30),
        state=state,
    )
    assert later is None

    # Earlier departure should resend
    earlier_now = datetime(2024, 10, 10, 7, 5, tzinfo=ZONE)
    earlier = evaluate_departure_notification(
        now=earlier_now,
        arrival_time=ARRIVAL_TIME,
        target_arrival=target_arrival,
        current_duration_mins=70.0,
        baseline_duration_mins=50.0,
        lead_time=timedelta(minutes=30),
        state=state,
    )
    assert earlier is not None


def test_pattern_alert_respects_cooldown() -> None:
    baseline = 12.0
    start = datetime(2024, 10, 10, 7, 30, tzinfo=ZONE)
    state = NotificationState()

    def step(offset_minutes: float, duration: float) -> PatternAlertDecision:
        return evaluate_pattern_alert(
            sample_time=start + timedelta(minutes=offset_minutes),
            current_duration_mins=duration,
            baseline_duration_mins=baseline,
            state=state,
            integral_threshold=25.0,
            deadband_minutes=2.0,
            cooldown_minutes=120.0,
        )

    first = step(0, 17.0)  # integral 15
    assert first.message is None
    assert first.state_changed

    second = step(5, 17.2)  # ~14.4 decayed + 16 crosses 25
    assert second.message is not None
    assert "sustained longer" in second.message
    assert state.pattern_alert_date == start.date()

    third = step(10, 30.0)  # well over threshold but inside the cooldown
    assert third.message is None

    fourth = step(130, 30.0)  # cooldown elapsed: a second event the same day can fire
    assert fourth.message is not None


def minutes(target_arrival: datetime, duration: float) -> float:
    recommended = target_arrival - timedelta(minutes=duration)
    midnight = recommended.replace(hour=0, minute=0, second=0, microsecond=0)
    return (recommended - midnight).total_seconds() / 60.0
