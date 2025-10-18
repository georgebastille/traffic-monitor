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


def test_pattern_alert_only_once_per_day() -> None:
    baseline = 12.0
    start = datetime(2024, 10, 10, 7, 30, tzinfo=ZONE)
    state = NotificationState()

    first: PatternAlertDecision = evaluate_pattern_alert(
        sample_time=start,
        current_duration_mins=17.0,
        baseline_duration_mins=baseline,
        state=state,
        integral_threshold=180.0,
        deadband_minutes=2.0,
    )
    assert first.message is None
    assert first.state_changed

    second: PatternAlertDecision = evaluate_pattern_alert(
        sample_time=start + timedelta(minutes=5),
        current_duration_mins=17.2,
        baseline_duration_mins=baseline,
        state=state,
        integral_threshold=180.0,
        deadband_minutes=2.0,
    )
    assert second.message is not None
    assert "Traffic pattern changed" in second.message
    assert state.pattern_alert_date == start.date()

    third: PatternAlertDecision = evaluate_pattern_alert(
        sample_time=start + timedelta(minutes=10),
        current_duration_mins=17.5,
        baseline_duration_mins=baseline,
        state=state,
        integral_threshold=180.0,
        deadband_minutes=2.0,
    )
    assert third.message is None


def minutes(target_arrival: datetime, duration: float) -> float:
    recommended = target_arrival - timedelta(minutes=duration)
    midnight = recommended.replace(hour=0, minute=0, second=0, microsecond=0)
    return (recommended - midnight).total_seconds() / 60.0
