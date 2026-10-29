from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import pandas as pd
from matplotlib.dates import DateFormatter, HourLocator
from matplotlib.figure import Figure


def plot_anomaly_to_png(
    jsonl_filename: Path | str,
    output_png: Path | str,
    *,
    timezone: str = "Europe/London",
) -> Path:
    """Render a weekday baseline vs. today's traffic line chart."""
    jsonl_path = Path(jsonl_filename)
    output_path = Path(output_png)
    frame = _load_frame(jsonl_path, timezone)
    frame["date"] = frame["query_time"].dt.date
    frame["dow"] = frame["query_time"].dt.dayofweek
    frame["time_bucket"] = frame["query_time"].dt.floor("5min")
    frame["time_of_day"] = frame["time_bucket"].dt.time

    today = frame["date"].max()
    dow_name = pd.to_datetime(today).strftime("%A")
    midnight = pd.Timestamp(today)
    end_of_day = midnight + pd.Timedelta(days=1)
    timeline = pd.date_range(midnight, end_of_day, freq="5min", inclusive="left")

    weekday_mask = frame["dow"] < 5
    history_mask = frame["date"] < today
    baseline = (
        frame[weekday_mask & history_mask]
        .groupby("time_of_day")["traffic_duration_mins"]
        .agg(mean="mean", std="std")
        .fillna(0.0)
    )

    baseline_mean = _timeline_lookup(timeline, baseline["mean"])
    baseline_std = _timeline_lookup(timeline, baseline["std"]).fillna(0.0)

    today_series = (
        frame[frame["date"] == today]
        .set_index("time_bucket")
        .groupby("time_bucket")["traffic_duration_mins"]
        .mean()
        .reindex(timeline)
    )

    figure = Figure(figsize=(11, 5))
    axis = figure.subplots()
    axis.plot(timeline, today_series, label="today (mins)")
    axis.plot(timeline, baseline_mean, linestyle="--", label="weekday baseline mean")

    lower = baseline_mean - baseline_std
    upper = baseline_mean + baseline_std
    axis.fill_between(timeline, lower, upper, alpha=0.2, label="weekday ±1σ")

    axis.set_xlim(midnight, end_of_day)
    axis.xaxis.set_major_locator(HourLocator(byhour=range(0, 24, 2)))
    axis.xaxis.set_major_formatter(DateFormatter("%H:%M"))
    axis.set_title(f"Travel time for {dow_name}")
    axis.set_xlabel("time of day")
    axis.set_ylabel("minutes")
    axis.grid(True, alpha=0.3)
    axis.legend()
    figure.tight_layout()
    figure.savefig(output_path, dpi=200, bbox_inches="tight", format="png")
    return output_path


def _timeline_lookup(timeline: Iterable[pd.Timestamp], source_series: pd.Series) -> pd.Series:
    """Match timeline timestamps to a grouped series keyed by time-of-day."""
    lookup = source_series.copy()
    times = [ts.time() for ts in timeline]
    return pd.Series(times, index=timeline).map(lookup)


def _load_frame(jsonl_path: Path, timezone: str) -> pd.DataFrame:
    """Load query times as naive local wall time; legacy naive timestamps are taken as already local."""
    zone = ZoneInfo(timezone)
    rows: list[tuple[datetime, float]] = []
    with jsonl_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                payload = json.loads(line)
                moment = datetime.fromisoformat(str(payload["query_time"]))
                duration = float(payload["traffic_duration_mins"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
            if moment.tzinfo is not None:
                moment = moment.astimezone(zone).replace(tzinfo=None)
            rows.append((moment, duration))
    if not rows:
        raise ValueError(f"No data available in {jsonl_path}")
    return pd.DataFrame(rows, columns=["query_time", "traffic_duration_mins"])
