from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from traffic_monitor import TomTomClient, TrafficMonitor, append_sample, plot_anomaly_to_png
from traffic_monitor.analytics import compute_bucket_ema_baseline, filter_recent_weekday_samples, load_samples, prune_jsonl_history
from traffic_monitor.config import AppConfig
from traffic_monitor.notifications import PatternAlertDecision, evaluate_departure_notification, evaluate_pattern_alert
from traffic_monitor.push import make_push_notifier
from traffic_monitor.state import NotificationState

DEPARTURE_LEAD = timedelta(minutes=30)


def log(message: str) -> None:
    print(f"[{datetime.now().isoformat()}] {message}")


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
        notifier = lambda msg: requests.post(f"https://ntfy.sh/{ntfy_topic}", data=msg.encode())
    else:
        notifier = lambda msg: log(f"[NOTIFY] {msg}")

    if route.provider == "google":
        api_key = os.getenv("GOOGLE_MAPS_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_MAPS_API_KEY is not set")
        monitor = TrafficMonitor.from_google_api_key(
            api_key,
            timezone=route.timezone,
            route_cache_path=route_cache,
            notifier=notifier,
        )
    else:
        api_key = os.getenv("TOMTOM_API_KEY")
        if not api_key:
            raise RuntimeError("TOMTOM_API_KEY is not set")
        monitor = TrafficMonitor(
            TomTomClient(api_key, timezone=route.timezone),
            timezone=route.timezone,
            route_cache_path=route_cache,
            notifier=notifier,
        )

    traffic_jsonl = data_dir / f"{route.id}_traffic.jsonl"
    traffic_png = data_dir / f"{route.id}_anomaly.png"
    state_path = data_dir / f"{route.id}_state.json"
    arrival_time = time.fromisoformat(route.arrival_time)

    current_sample = monitor.get_traffic_data(route.origin, route.destination)
    append_sample(traffic_jsonl, current_sample)
    plot_anomaly_to_png(traffic_jsonl, traffic_png)
    log(f"Updated baseline series at {traffic_jsonl}")
    now = datetime.now(timezone)

    history_days_env = os.getenv("HISTORY_DAYS")
    try:
        history_days = int(history_days_env) if history_days_env else 90
    except ValueError:
        history_days = 90
        log(f"Ignoring invalid HISTORY_DAYS={history_days_env!r}")
    cutoff = now - timedelta(days=history_days)
    removed = prune_jsonl_history(traffic_jsonl, cutoff=cutoff)
    if removed:
        log(f"Pruned {removed} entries older than {cutoff.date()} from {traffic_jsonl}")

    target_arrival = _resolve_target_arrival(now, arrival_time, timezone)

    traffic_samples = load_samples(traffic_jsonl, tzinfo=timezone)
    historical_samples = [sample for sample in traffic_samples if sample.query_time < current_sample.query_time]
    recent_samples = filter_recent_weekday_samples(historical_samples, reference=now)
    percentile_env = os.getenv("BASELINE_PERCENTILE")
    try:
        baseline_percentile = int(percentile_env) if percentile_env else 75
    except ValueError:
        baseline_percentile = 75
        log(f"Ignoring invalid BASELINE_PERCENTILE={percentile_env!r}")

    baseline_duration = compute_bucket_ema_baseline(
        recent_samples,
        target_departure=current_sample.departure_time,
        max_weekdays=5,
        bucket_minutes=5,
        ema_span=3,
        baseline_percentile=baseline_percentile,
    ) or current_sample.traffic_duration_mins

    state = NotificationState.load(state_path)
    state_changed = False

    departure_notice = evaluate_departure_notification(
        now=now,
        arrival_time=arrival_time,
        target_arrival=target_arrival,
        current_duration_mins=current_sample.traffic_duration_mins,
        baseline_duration_mins=baseline_duration,
        lead_time=DEPARTURE_LEAD,
        state=state,
    )
    if departure_notice:
        monitor.notify(departure_notice.message)
        state.departure_date = target_arrival.date()
        state.departure_minutes = departure_notice.departure_minutes
        state_changed = True
        log("Sent departure notification")

    threshold_env = os.getenv("TRAFFIC_ANOMALY_THRESHOLD")
    try:
        integral_threshold = float(threshold_env) if threshold_env else 180.0
    except ValueError:
        integral_threshold = 180.0
        log(f"Ignoring invalid TRAFFIC_ANOMALY_THRESHOLD={threshold_env!r}")

    deadband_env = os.getenv("TRAFFIC_ANOMALY_DEADBAND")
    try:
        anomaly_deadband = float(deadband_env) if deadband_env else 2.0
    except ValueError:
        anomaly_deadband = 2.0
        log(f"Ignoring invalid TRAFFIC_ANOMALY_DEADBAND={deadband_env!r}")

    decay_env = os.getenv("TRAFFIC_ANOMALY_DECAY_MINUTES")
    try:
        anomaly_decay = float(decay_env) if decay_env else 120.0
    except ValueError:
        anomaly_decay = 120.0
        log(f"Ignoring invalid TRAFFIC_ANOMALY_DECAY_MINUTES={decay_env!r}")

    pattern_decision: PatternAlertDecision = evaluate_pattern_alert(
        sample_time=current_sample.query_time,
        current_duration_mins=current_sample.traffic_duration_mins,
        baseline_duration_mins=baseline_duration,
        state=state,
        integral_threshold=integral_threshold,
        deadband_minutes=anomaly_deadband,
        decay_minutes=anomaly_decay,
    )
    if pattern_decision.state_changed:
        state_changed = True
    if pattern_decision.message:
        monitor.notify(pattern_decision.message)
        state_changed = True
        log("Sent pattern change notification")

    if state_changed:
        state.save(state_path)
        log(f"Persisted notification state at {state_path}")


if __name__ == "__main__":
    main()
