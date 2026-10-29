from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from traffic_monitor import TomTomClient, TrafficMonitor, append_sample
from traffic_monitor.analytics import (
    compute_bucket_ema_baseline,
    filter_recent_weekday_samples,
    load_samples,
    predict_departure_duration,
)
from traffic_monitor.config import AppConfig
from traffic_monitor.notifications import (
    Notification,
    PatternAlertDecision,
    evaluate_departure_notification,
    evaluate_pattern_alert,
)
from traffic_monitor.ntfy import send_ntfy
from traffic_monitor.push import make_push_notifier
from traffic_monitor.state import NotificationState

DEPARTURE_LEAD = timedelta(minutes=30)


def log(message: str) -> None:
    print(f"[{datetime.now().isoformat()}] {message}")


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        log(f"Ignoring invalid {name}={raw!r}")
        return default


def _resolve_target_arrival(now: datetime, arrival_time: time, timezone: ZoneInfo) -> datetime:
    target_date = now.date()
    candidate = datetime.combine(target_date, arrival_time, tzinfo=timezone)
    if now > candidate:
        next_day = _next_weekday(target_date + timedelta(days=1))
        candidate = datetime.combine(next_day, arrival_time, tzinfo=timezone)
    return candidate


def _next_weekday(start: date) -> date:
    current = start
    while current.weekday() >= 5:
        current += timedelta(days=1)
    return current


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

    current_sample = monitor.get_traffic_data(route.origin, route.destination)
    append_sample(traffic_jsonl, current_sample)
    log(f"Appended sample to {traffic_jsonl}")
    now = datetime.now(timezone)

    target_arrival = _resolve_target_arrival(now, arrival_time, timezone)

    traffic_samples = load_samples(traffic_jsonl, tzinfo=timezone)
    historical_samples = [sample for sample in traffic_samples if sample.query_time < current_sample.query_time]
    recent_samples = filter_recent_weekday_samples(historical_samples, reference=now)
    baseline_percentile = int(_env_float("BASELINE_PERCENTILE", 75))

    baseline_duration = (
        compute_bucket_ema_baseline(
            recent_samples,
            target_departure=current_sample.departure_time,
            max_weekdays=5,
            bucket_minutes=5,
            ema_span=3,
            baseline_percentile=baseline_percentile,
        )
        or current_sample.traffic_duration_mins
    )

    predicted_duration, departure_baseline = predict_departure_duration(
        recent_samples,
        current_duration_mins=current_sample.traffic_duration_mins,
        current_baseline_mins=baseline_duration,
        target_arrival=target_arrival,
        baseline_percentile=baseline_percentile,
    )

    orange_mins = _env_float("TRAFFIC_ORANGE_MINUTES", 2.0)
    red_mins = _env_float("TRAFFIC_RED_MINUTES", 5.0)

    state = NotificationState.load(state_path)
    state_changed = False

    departure_notice = evaluate_departure_notification(
        now=now,
        arrival_time=arrival_time,
        target_arrival=target_arrival,
        current_duration_mins=predicted_duration,
        baseline_duration_mins=departure_baseline,
        lead_time=DEPARTURE_LEAD,
        state=state,
        orange_mins=orange_mins,
        red_mins=red_mins,
    )
    if departure_notice:
        notifier(departure_notice.notification)
        state.departure_date = target_arrival.date()
        state.departure_minutes = departure_notice.departure_minutes
        state.departure_level = departure_notice.notification.level
        state_changed = True
        log(f"Sent departure notification: {departure_notice.notification.title}")

    integral_threshold = _env_float("TRAFFIC_ANOMALY_THRESHOLD", 90.0)
    anomaly_deadband = _env_float("TRAFFIC_ANOMALY_DEADBAND", 2.0)
    anomaly_decay = _env_float("TRAFFIC_ANOMALY_DECAY_MINUTES", 120.0)
    alert_cooldown = _env_float("TRAFFIC_ALERT_COOLDOWN_MINUTES", 120.0)

    pattern_decision: PatternAlertDecision = evaluate_pattern_alert(
        sample_time=current_sample.query_time,
        current_duration_mins=current_sample.traffic_duration_mins,
        baseline_duration_mins=baseline_duration,
        state=state,
        integral_threshold=integral_threshold,
        deadband_minutes=anomaly_deadband,
        decay_minutes=anomaly_decay,
        cooldown_minutes=alert_cooldown,
        orange_mins=orange_mins,
        red_mins=red_mins,
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
