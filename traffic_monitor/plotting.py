from __future__ import annotations

from pathlib import Path
from typing import Iterable

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.dates import DateFormatter, HourLocator


def plot_to_png(jsonl_filename: Path | str, output_png: Path | str) -> Path:
    """Plot historical clear vs traffic durations and save as PNG."""
    jsonl_path = Path(jsonl_filename)
    output_path = Path(output_png)
    frame = _load_frame(jsonl_path)
    frame = frame.set_index("query_time")
    axis = frame[["clear_duration_mins", "traffic_duration_mins"]].plot(
        title="Traffic Duration Over Time",
        ylabel="Duration (minutes)",
        xlabel="Time",
        figsize=(10, 6),
    )
    axis.grid(True)
    figure = axis.get_figure()
    figure.savefig(output_path)
    plt.close(figure)
    return output_path


def plot_anomaly_to_png(jsonl_filename: Path | str, output_png: Path | str) -> Path:
    """Render a weekday baseline vs. today's traffic line chart."""
    jsonl_path = Path(jsonl_filename)
    output_path = Path(output_png)
    frame = _load_frame(jsonl_path)
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

    figure, axis = plt.subplots(figsize=(11, 5))
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
    figure.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close(figure)
    return output_path


def _timeline_lookup(timeline: Iterable[pd.Timestamp], source_series: pd.Series) -> pd.Series:
    """Match timeline timestamps to a grouped series keyed by time-of-day."""
    lookup = source_series.copy()
    times = [ts.time() for ts in timeline]
    return pd.Series(times, index=timeline).map(lookup)


def _load_frame(jsonl_path: Path) -> pd.DataFrame:
    frame = pd.read_json(jsonl_path, lines=True)
    if frame.empty:
        raise ValueError(f"No data available in {jsonl_path}")
    frame["query_time"] = _normalize_query_time(frame["query_time"])
    return frame


def _normalize_query_time(series: pd.Series) -> pd.Series:
    """Handle legacy naive timestamps and newer timezone-aware ISO strings."""
    raw = series.astype(str)
    tz_mask = raw.str.contains(r"(?:Z|[+-]\d{2}:\d{2})$", na=False)
    normalized = pd.Series(index=series.index, dtype="datetime64[ns]")

    if tz_mask.any():
        aware_idx = pd.to_datetime(raw[tz_mask], format="ISO8601")
        aware_values = [pd.Timestamp(value).to_pydatetime().replace(tzinfo=None) for value in aware_idx]
        normalized.loc[tz_mask] = aware_values

    if (~tz_mask).any():
        naive_idx = pd.to_datetime(raw[~tz_mask], format="ISO8601")
        naive_values = [pd.Timestamp(value).to_pydatetime() for value in naive_idx]
        normalized.loc[~tz_mask] = naive_values

    return normalized.astype("datetime64[ns]")
