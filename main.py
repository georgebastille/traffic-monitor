from __future__ import annotations

import os
from datetime import datetime, time
from pathlib import Path

from dotenv import load_dotenv

from traffic_monitor import TrafficMonitor, append_sample, plot_anomaly_to_png

HOME_ORIGIN = "164 Devonshire Road, London SE23 3SZ"
SCHOOL_DESTINATION = "Rosemead Preparatory School, 70 Thurlow Park Road, London SE21 8HZ"
TRAFFIC_JSONL = Path("traffic_report.jsonl")
TRAFFIC_PNG = Path("traffic_report_anomaly.png")
ARRIVAL_JSONL = Path("traffic_report_arrival.jsonl")
ARRIVAL_PNG = Path("traffic_report_arrival.png")
ALERT_THRESHOLD_MINS = 20.0


def log(message: str) -> None:
    print(f"[{datetime.now().isoformat()}] {message}")


def main() -> None:
    load_dotenv()
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_MAPS_API_KEY is not set")

    monitor = TrafficMonitor.from_api_key(api_key)
    baseline_sample = monitor.get_traffic_data(HOME_ORIGIN, SCHOOL_DESTINATION)
    append_sample(TRAFFIC_JSONL, baseline_sample)
    plot_anomaly_to_png(TRAFFIC_JSONL, TRAFFIC_PNG)
    log(f"Updated baseline series at {TRAFFIC_JSONL}")

    arrival_sample = monitor.get_traffic_data(
        HOME_ORIGIN,
        SCHOOL_DESTINATION,
        departure_time=time(8, 0),
    )
    append_sample(ARRIVAL_JSONL, arrival_sample)
    plot_anomaly_to_png(ARRIVAL_JSONL, ARRIVAL_PNG)
    log(f"Updated arrival series at {ARRIVAL_JSONL}")

    if arrival_sample.traffic_duration_mins > ALERT_THRESHOLD_MINS:
        monitor.notify(
            f"Traffic alert! Expected travel time is {arrival_sample.traffic_duration_mins:.1f} mins."
        )
        log("Sent traffic alert notification")


if __name__ == "__main__":
    main()
