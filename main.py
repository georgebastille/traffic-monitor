from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from traffic_monitor import TrafficMonitor, append_sample, plot_anomaly_to_png
from traffic_monitor.analytics import (
    compute_baseline_duration,
    compute_ema,
    compute_time_of_day_stats,
    filter_recent_weekday_samples,
    load_samples,
    minutes_since_midnight,
)
from traffic_monitor.notifications import evaluate_departure_notification, evaluate_pattern_alert
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


def main() -> None:
    load_dotenv()
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_MAPS_API_KEY is not set")

    monitor = TrafficMonitor.from_api_key(api_key)
    current_sample = monitor.get_traffic_data(HOME_ORIGIN, SCHOOL_DESTINATION)
    append_sample(TRAFFIC_JSONL, current_sample)
    plot_anomaly_to_png(TRAFFIC_JSONL, TRAFFIC_PNG)
    log(f"Updated baseline series at {TRAFFIC_JSONL}")

    now = datetime.now(TIMEZONE)
    target_arrival = _resolve_target_arrival(now)

    traffic_samples = load_samples(TRAFFIC_JSONL, tzinfo=TIMEZONE)
    recent_samples = filter_recent_weekday_samples(traffic_samples, reference=now)
    baseline_duration = compute_baseline_duration(recent_samples) or current_sample.traffic_duration_mins
    ema_duration = compute_ema(recent_samples)
    stats = compute_time_of_day_stats(
        recent_samples,
        target_minutes=minutes_since_midnight(current_sample.departure_time),
    )

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

    pattern_alert = evaluate_pattern_alert(
        now=now,
        ema_minutes=ema_duration,
        stats=stats,
        state=state,
    )
    if pattern_alert:
        monitor.notify(pattern_alert)
        state.pattern_alert_date = now.date()
        state_changed = True
        log("Sent pattern change notification")

    if state_changed:
        state.save(STATE_PATH)
        log(f"Persisted notification state at {STATE_PATH}")


if __name__ == "__main__":
    main()
