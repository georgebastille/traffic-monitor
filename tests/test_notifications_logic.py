from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from traffic_monitor.notifications import evaluate_departure_notification, evaluate_pattern_alert
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
    now = datetime(2024, 10, 10, 7, 30, tzinfo=ZONE)
    state = NotificationState()

    first = evaluate_pattern_alert(
        now=now,
        ema_minutes=30.0,
        stats=(20.0, 4.0),
        state=state,
    )
    assert first is not None

    state.pattern_alert_date = now.date()
    second = evaluate_pattern_alert(
        now=now + timedelta(minutes=10),
        ema_minutes=35.0,
        stats=(20.0, 4.0),
        state=state,
    )
    assert second is None


def minutes(target_arrival: datetime, duration: float) -> float:
    recommended = target_arrival - timedelta(minutes=duration)
    midnight = recommended.replace(hour=0, minute=0, second=0, microsecond=0)
    return (recommended - midnight).total_seconds() / 60.0
