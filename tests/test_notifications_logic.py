from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from traffic_monitor.notifications import (
    PatternAlertDecision,
    evaluate_departure_notification,
    evaluate_pattern_alert,
    traffic_level,
)
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
    # 60 mins against a 50 min baseline is a +10 min delay.
    assert decision.notification.level == "red"
    assert decision.notification.title == "Leave 07:20 · +10m (60m) · check Waze"
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
    assert first.notification is None
    assert first.state_changed

    second = step(5, 17.2)  # ~14.4 decayed + 16 crosses 25
    assert second.notification is not None
    assert second.notification.level == "red"
    assert second.notification.title == "Traffic +5m now (17m vs 12m usual)"
    assert state.pattern_alert_date == start.date()

    third = step(10, 30.0)  # well over threshold but inside the cooldown
    assert third.notification is None

    fourth = step(130, 30.0)  # cooldown elapsed: a second event the same day can fire
    assert fourth.notification is not None


def minutes(target_arrival: datetime, duration: float) -> float:
    recommended = target_arrival - timedelta(minutes=duration)
    midnight = recommended.replace(hour=0, minute=0, second=0, microsecond=0)
    return (recommended - midnight).total_seconds() / 60.0


def _departure(now: datetime, duration: float, baseline: float, state: NotificationState):
    target_arrival = datetime(2024, 10, 10, ARRIVAL_TIME.hour, ARRIVAL_TIME.minute, tzinfo=ZONE)
    return evaluate_departure_notification(
        now=now,
        arrival_time=ARRIVAL_TIME,
        target_arrival=target_arrival,
        current_duration_mins=duration,
        baseline_duration_mins=baseline,
        lead_time=timedelta(minutes=30),
        state=state,
    )


def test_traffic_level_thresholds() -> None:
    assert [traffic_level(delay) for delay in (-3.0, 1.9, 2.0, 4.9, 5.0)] == [
        "green",
        "green",
        "orange",
        "orange",
        "red",
    ]


def test_departure_notification_titles_lead_with_level_and_leave_time() -> None:
    now = datetime(2024, 10, 10, 7, 40, tzinfo=ZONE)
    green = _departure(now, 14.0, 14.0, NotificationState())
    orange = _departure(now, 18.0, 15.0, NotificationState())

    assert green is not None and green.notification.level == "green"
    assert green.notification.title == "Leave 08:06 · normal (14m)"
    assert orange is not None and orange.notification.level == "orange"
    assert orange.notification.title == "Leave 08:02 · +3m (18m)"


def test_departure_notification_resends_only_when_worse_or_much_earlier() -> None:
    state = NotificationState()
    first = _departure(datetime(2024, 10, 10, 7, 40, tzinfo=ZONE), 14.0, 14.0, state)
    assert first is not None
    state.departure_date = date(2024, 10, 10)
    state.departure_minutes = first.departure_minutes
    state.departure_level = first.notification.level

    # One minute earlier, still green: stay quiet.
    assert _departure(datetime(2024, 10, 10, 7, 45, tzinfo=ZONE), 15.0, 14.0, state) is None
    # Level worsens to orange: resend even though only 2 minutes earlier.
    assert _departure(datetime(2024, 10, 10, 7, 45, tzinfo=ZONE), 16.5, 14.0, state) is not None
    # Same level but 3+ minutes earlier: resend.
    assert _departure(datetime(2024, 10, 10, 7, 45, tzinfo=ZONE), 17.0, 16.0, state) is not None


def test_pattern_alert_for_shorter_trips_is_green() -> None:
    state = NotificationState()
    start = datetime(2024, 10, 10, 10, 0, tzinfo=ZONE)
    decision = None
    for step in range(10):
        decision = evaluate_pattern_alert(
            sample_time=start + timedelta(minutes=5 * step),
            current_duration_mins=8.0,
            baseline_duration_mins=14.0,
            state=state,
        )
        if decision.notification:
            break

    assert decision is not None and decision.notification is not None
    assert decision.notification.level == "green"
    assert decision.notification.title == "Traffic eased (8m vs 14m usual)"
