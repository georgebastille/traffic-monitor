from __future__ import annotations

import os
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from traffic_monitor import TomTomClient, TrafficMonitor, append_sample
from traffic_monitor.analytics import load_samples
from traffic_monitor.config import AppConfig
from traffic_monitor.notifications import (
    Notification,
    PatternAlertDecision,
    evaluate_departure_notification,
    evaluate_pattern_alert,
)
from traffic_monitor.ntfy import send_ntfy
from traffic_monitor.push import make_push_notifier
from traffic_monitor.settings import Settings
from traffic_monitor.state import NotificationState
from traffic_monitor.status import assess

DEPARTURE_LEAD = timedelta(minutes=30)


def log(message: str) -> None:
    print(f"[{datetime.now().isoformat()}] {message}")


def main(argv: Sequence[str] | None = None) -> None:
    load_dotenv()

    data_dir = Path(os.getenv("DATA_DIR", "."))
    config_path = data_dir / "routes.json"
    config = AppConfig.load(config_path)

    route = config.active_route
    if route is None:
        log("No active route configured. Exiting.")
        return

    timezone = ZoneInfo(route.timezone)
    route_cache = data_dir / f"{route.id}_baseline.json"
    push_sub_path = data_dir / "push_subscription.json"

    vapid_private_key = os.getenv("VAPID_PRIVATE_KEY", "")
    vapid_mailto = os.getenv("VAPID_MAILTO", "mailto:user@example.com")
    ntfy_topic = os.getenv("NTFY_TOPIC", "")
    if vapid_private_key:
        notifier = make_push_notifier(
            push_sub_path,
            vapid_private_key=vapid_private_key,
            vapid_claims={"sub": vapid_mailto},
        )
    elif ntfy_topic:

        def notifier(notification: Notification) -> None:
            send_ntfy(ntfy_topic, notification)

    else:

        def notifier(notification: Notification) -> None:
            log(f"[NOTIFY] {notification.emoji} {notification.title} | {notification.body}")

    api_key = os.getenv("TOMTOM_API_KEY")
    if not api_key:
        raise RuntimeError("TOMTOM_API_KEY is not set")
    monitor = TrafficMonitor(
        TomTomClient(api_key, timezone=route.timezone, geocode_cache_path=data_dir / "geocode_cache.json"),
        timezone=route.timezone,
        route_cache_path=route_cache,
    )

    traffic_jsonl = data_dir / f"{route.id}_traffic.jsonl"
    state_path = data_dir / f"{route.id}_state.json"
    arrival_time = time.fromisoformat(route.arrival_time)
    settings = Settings.from_env()

    current_sample = monitor.get_traffic_data(route.origin, route.destination)
    append_sample(traffic_jsonl, current_sample)
    log(f"Appended sample to {traffic_jsonl}")
    now = datetime.now(timezone)

    traffic_samples = load_samples(traffic_jsonl, tzinfo=timezone)
    historical_samples = [sample for sample in traffic_samples if sample.query_time < current_sample.query_time]
    assessment = assess(
        historical_samples, current_sample, now=now, arrival_time=arrival_time, zone=timezone, settings=settings
    )

    state = NotificationState.load(state_path)
    state_changed = False

    departure_notice = evaluate_departure_notification(
        now=now,
        arrival_time=arrival_time,
        target_arrival=assessment.target_arrival,
        current_duration_mins=assessment.predicted_mins,
        baseline_duration_mins=assessment.departure_typical_mins,
        lead_time=DEPARTURE_LEAD,
        state=state,
        orange_mins=settings.orange_mins,
        red_mins=settings.red_mins,
    )
    if departure_notice:
        notifier(departure_notice.notification)
        state.departure_date = assessment.target_arrival.date()
        state.departure_minutes = departure_notice.departure_minutes
        state.departure_level = departure_notice.notification.level
        state_changed = True
        log(f"Sent departure notification: {departure_notice.notification.title}")

    pattern_decision: PatternAlertDecision = evaluate_pattern_alert(
        sample_time=current_sample.query_time,
        current_duration_mins=current_sample.traffic_duration_mins,
        baseline_duration_mins=assessment.typical_mins,
        state=state,
        integral_threshold=settings.anomaly_threshold,
        deadband_minutes=settings.anomaly_deadband,
        decay_minutes=settings.anomaly_decay_minutes,
        cooldown_minutes=settings.alert_cooldown_minutes,
        orange_mins=settings.orange_mins,
        red_mins=settings.red_mins,
    )
    if pattern_decision.state_changed:
        state_changed = True
    if pattern_decision.notification:
        notifier(pattern_decision.notification)
        state_changed = True
        log(f"Sent pattern change notification: {pattern_decision.notification.title}")

    if state_changed:
        state.save(state_path)
        log(f"Persisted notification state at {state_path}")


if __name__ == "__main__":
    main()
