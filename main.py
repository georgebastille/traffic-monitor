import json
import os
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import googlemaps
import matplotlib.pyplot as plt
import pandas as pd
import requests
from dotenv import load_dotenv
from matplotlib.dates import DateFormatter, HourLocator

NTFY_TOPIC = "traffic_monitor"


class TrafficMonitor:
    """
    https://github.com/googlemaps/google-maps-services-python?tab=readme-ov-file
    """

    def __init__(self, api_key: str):
        self.gmaps = googlemaps.Client(key=api_key)

    def notify(self, msg):
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=msg.encode())

    def get_traffic_data(self, origin: str, destination: str, departure_time: time | None = None):
        # Request directions via public transit
        if not departure_time:
            departure_time = "now"
            departure_datetime = datetime.now()
        else:
            departure_time, departure_datetime = self._next_arrival_epoch(departure_time)
        directions_result = self.gmaps.distance_matrix(
            origin,
            destination,
            mode="driving",
            departure_time=departure_time,
            traffic_model="pessimistic",
        )
        origin_address = directions_result["origin_addresses"][0]
        destination_address = directions_result["destination_addresses"][0]
        clear_duration_secs = directions_result["rows"][0]["elements"][0]["duration"]["value"]
        traffic_duration_secs = directions_result["rows"][0]["elements"][0]["duration_in_traffic"]["value"]

        return {
            "query_time": datetime.now().isoformat(),
            "departure_time": departure_datetime.isoformat(),
            "origin": origin_address,
            "destination": destination_address,
            "clear_duration_mins": clear_duration_secs / 60,
            "traffic_duration_mins": traffic_duration_secs / 60,
        }

    def _next_arrival_epoch(
        self,
        target_time: time | str,
        tz_name: str = "Europe/London",
    ) -> int:
        """Return epoch seconds for the next occurrence of target_time in tz_name."""
        if isinstance(target_time, str):
            # Accept "HH:MM" or "HH:MM:SS"
            target_time = time.fromisoformat(target_time)

        tz = ZoneInfo(tz_name)
        now = datetime.now(tz)

        # Build today's target in the same timezone
        today_target = now.replace(
            hour=target_time.hour,
            minute=target_time.minute,
            second=getattr(target_time, "second", 0),
            microsecond=0,
        )

        target_dt = today_target if now <= today_target else today_target + timedelta(days=1)

        # Convert to Unix epoch seconds
        return (int(target_dt.timestamp()), target_dt)


def plot_to_png(jsonl_filename: str, output_png: str):
    df = pd.read_json(jsonl_filename, lines=True)
    df["query_time"] = pd.to_datetime(df["query_time"])
    df = df.set_index("query_time")
    ax = df[["clear_duration_mins", "traffic_duration_mins"]].plot(
        title="Traffic Duration Over Time",
        ylabel="Duration (minutes)",
        xlabel="Time",
        figsize=(10, 6),
    )
    ax.grid(True)
    fig = ax.get_figure()
    fig.savefig(output_png)
    print(f"Saved plot to {output_png}")


def plot_anomaly_to_png(jsonl_filename: str, output_png: str):
    df = pd.read_json(jsonl_filename, lines=True)

    # --- Parse & features ---
    df["query_time"] = pd.to_datetime(df["query_time"])
    df["date"] = df["query_time"].dt.date
    df["dow"] = df["query_time"].dt.dayofweek  # 0=Mon ... 6=Sun
    df["time_bucket"] = df["query_time"].dt.floor("5min")
    df["time_of_day"] = df["time_bucket"].dt.time

    today = df["date"].max()
    dow_name = pd.to_datetime(today).strftime("%A")

    # Build a fixed midnight→midnight x-axis for today (5-min buckets)
    midnight = pd.Timestamp(today)
    end_of_day = midnight + pd.Timedelta(days=1)
    timeline = pd.date_range(midnight, end_of_day, freq="5min", inclusive="left")

    # --- Build WEEKDAY baseline (Mon–Fri), from all past data (exclude today) ---
    weekday_mask = df["dow"] < 5
    history_mask = df["date"] < today  # avoid peeking at today
    baseline_df = (
        df[weekday_mask & history_mask]
        .groupby("time_of_day")["traffic_duration_mins"]
        .agg(mean="mean", std="std", count="count")
        .reset_index()
    )
    baseline_df["std"] = baseline_df["std"].fillna(0.0)

    # Expand baseline to the full day timeline by matching on time-of-day
    baseline_map = baseline_df.set_index("time_of_day")
    tod_series = pd.Series([ts.time() for ts in timeline], index=timeline, name="time_of_day")
    baseline_mean = tod_series.map(baseline_map["mean"])
    baseline_std = tod_series.map(baseline_map["std"]).fillna(0.0)

    # --- Today’s series, aligned to 5-min buckets ---
    today_df = (
        df[df["date"] == today]
        .assign(bucket=lambda x: x["query_time"].dt.floor("5min"))
        .sort_values("query_time")
        .copy()
    )

    # If there are multiple samples in a bucket, average them
    today_series = (
        today_df.groupby("bucket")["traffic_duration_mins"]
        .mean()
        .reindex(timeline)  # align to full-day axis (NaN where no data yet)
    )

    # --- Plot: baseline runs midnight→midnight; today shows only available data ---
    plt.figure(figsize=(11, 5))

    # Today line (will naturally stop at the latest timestamp we have)
    plt.plot(timeline, today_series, label="today (mins)")

    # Baseline mean line (full-day)
    plt.plot(timeline, baseline_mean, linestyle="--", label="weekday baseline mean")

    # Shaded ±1σ band across the full day (gaps where baseline missing)
    lower = baseline_mean - baseline_std
    upper = baseline_mean + baseline_std
    plt.fill_between(timeline, lower, upper, alpha=0.2, label="weekday ±1σ")

    # --- Formatting: x-axis is strictly today's time, midnight→midnight, time labels only ---
    plt.xlim(midnight, end_of_day)
    plt.gca().xaxis.set_major_locator(HourLocator(byhour=range(0, 24, 2)))  # every 2 hours
    plt.gca().xaxis.set_major_formatter(DateFormatter("%H:%M"))

    plt.title(f"Travel time for {dow_name}")
    plt.xlabel("time of day")
    plt.ylabel("minutes")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.show()
    plt.tight_layout()

    plt.savefig(output_png, dpi=200, bbox_inches="tight")


def main():
    load_dotenv()  # take environment variables
    google_api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    traffic_monitor = TrafficMonitor(api_key=google_api_key)
    response = traffic_monitor.get_traffic_data(
        "164 Devonshire Road, London SE23 3SZ",
        "Rosemead Preparatory School, 70 Thurlow Park Road, London SE21 8HZ",
    )

    output_jsonl_filename = "traffic_report.jsonl"

    with open(output_jsonl_filename, "a") as f:
        f.write(f"{json.dumps(response)}\n")
    print(f"Appended traffic data to {output_jsonl_filename}")
    # plot_to_png(output_jsonl_filename, "traffic_report.png")
    plot_anomaly_to_png(output_jsonl_filename, "traffic_report_anomaly.png")

    arrival_response = traffic_monitor.get_traffic_data(
        "164 Devonshire Road, London SE23 3SZ",
        "Rosemead Preparatory School, 70 Thurlow Park Road, London SE21 8HZ",
        departure_time=time(8, 00),
    )
    if arrival_response["traffic_duration_mins"] > 20:
        print("Sending traffic alert notification")
        traffic_monitor.notify(
            f"Traffic alert! Expected travel time is {arrival_response['traffic_duration_mins']:.1f} mins."
        )

    output_jsonl_arrival_filename = "traffic_report_arrival.jsonl"

    with open(output_jsonl_arrival_filename, "a") as f:
        f.write(f"{json.dumps(arrival_response)}\n")
    print(f"Appended traffic arrival data to {output_jsonl_arrival_filename}")
    plot_anomaly_to_png(output_jsonl_arrival_filename, "traffic_report_arrival.png")


if __name__ == "__main__":
    main()
