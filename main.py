from __future__ import annotations

import os
import argparse
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from traffic_monitor import TomTomClient, TrafficMonitor, append_sample, plot_anomaly_to_png
from traffic_monitor.analytics import compute_bucket_ema_baseline, filter_recent_weekday_samples, load_samples, prune_jsonl_history
from traffic_monitor.notifications import PatternAlertDecision, evaluate_departure_notification, evaluate_pattern_alert
from traffic_monitor.state import NotificationState

HOME_ORIGIN = "164 Devonshire Road, London SE23 3SZ"
SCHOOL_DESTINATION = "Rosemead Preparatory School, 70 Thurlow Park Road, London SE21 8HZ"
TRAFFIC_JSONL = Path("traffic_report.jsonl")
TRAFFIC_PNG = Path("traffic_report_anomaly.png")
ARRIVAL_TIME = time(8, 20)
DEPARTURE_LEAD = timedelta(minutes=30)
STATE_PATH = Path("traffic_notification_state.json")
TIMEZONE = ZoneInfo("Europe/London")


def log(message: str) -> None:
    print(f"[{datetime.now().isoformat()}] {message}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Traffic monitor")
    parser.add_argument(
        "--provider",
        choices=["google", "tomtom"],
        default="tomtom",
        help="Routing provider to use (default: google)",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def _resolve_target_arrival(now: datetime) -> datetime:
    target_date = now.date()
    candidate = datetime.combine(target_date, ARRIVAL_TIME, tzinfo=TIMEZONE)
    if now > candidate:
        next_day = _next_weekday(target_date + timedelta(days=1))
        candidate = datetime.combine(next_day, ARRIVAL_TIME, tzinfo=TIMEZONE)
    return candidate


def _next_weekday(start: date) -> date:
    current = start
    while current.weekday() >= 5:
        current += timedelta(days=1)
    return current


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    load_dotenv()
    if args.provider == "google":
        api_key = os.getenv("GOOGLE_MAPS_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_MAPS_API_KEY is not set")
        monitor = TrafficMonitor.from_google_api_key(api_key)
    else:
        api_key = os.getenv("TOMTOM_API_KEY")
        if not api_key:
            raise RuntimeError("TOMTOM_API_KEY is not set")
        monitor = TrafficMonitor(
            TomTomClient(api_key, timezone="Europe/London"),
            timezone="Europe/London",
    )
    current_sample = monitor.get_traffic_data(HOME_ORIGIN, SCHOOL_DESTINATION)
    append_sample(TRAFFIC_JSONL, current_sample)
    plot_anomaly_to_png(TRAFFIC_JSONL, TRAFFIC_PNG)
    log(f"Updated baseline series at {TRAFFIC_JSONL}")
    now = datetime.now(TIMEZONE)

    cutoff = now - timedelta(days=14)
    removed = prune_jsonl_history(TRAFFIC_JSONL, cutoff=cutoff)
    if removed:
        log(f"Pruned {removed} entries older than {cutoff.date()} from {TRAFFIC_JSONL}")

    target_arrival = _resolve_target_arrival(now)

    traffic_samples = load_samples(TRAFFIC_JSONL, tzinfo=TIMEZONE)
    historical_samples = [sample for sample in traffic_samples if sample.query_time < current_sample.query_time]
    recent_samples = filter_recent_weekday_samples(historical_samples, reference=now)
    baseline_duration = compute_bucket_ema_baseline(
        recent_samples,
        target_departure=current_sample.departure_time,
        max_weekdays=5,
        bucket_minutes=5,
        ema_span=5,
    ) or current_sample.traffic_duration_mins

    state = NotificationState.load(STATE_PATH)
    state_changed = False

    departure_notice = evaluate_departure_notification(
        now=now,
        arrival_time=ARRIVAL_TIME,
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
        state.save(STATE_PATH)
        log(f"Persisted notification state at {STATE_PATH}")


if __name__ == "__main__":
    main()
