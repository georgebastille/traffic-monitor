from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"[{datetime.now().isoformat()}] Ignoring invalid {name}={raw!r}")
        return default


@dataclass(frozen=True)
class Settings:
    """Tunables shared by the monitor and the API, read from the environment."""

    # Leave-by uses a cautious P75; change detection compares against the median, since a P75
    # baseline made ordinary traffic read as "eased" (33 of 60 alerts in a Mar-Oct 2026 replay).
    departure_percentile: int = 75
    alert_percentile: int = 50
    baseline_window_minutes: int = 10
    orange_mins: float = 2.0
    red_mins: float = 5.0
    anomaly_threshold: float = 90.0
    anomaly_deadband: float = 2.0
    anomaly_decay_minutes: float = 120.0
    alert_cooldown_minutes: float = 120.0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            departure_percentile=int(env_float("DEPARTURE_BASELINE_PERCENTILE", cls.departure_percentile)),
            alert_percentile=int(env_float("ALERT_BASELINE_PERCENTILE", cls.alert_percentile)),
            baseline_window_minutes=int(env_float("BASELINE_WINDOW_MINUTES", cls.baseline_window_minutes)),
            orange_mins=env_float("TRAFFIC_ORANGE_MINUTES", cls.orange_mins),
            red_mins=env_float("TRAFFIC_RED_MINUTES", cls.red_mins),
            anomaly_threshold=env_float("TRAFFIC_ANOMALY_THRESHOLD", cls.anomaly_threshold),
            anomaly_deadband=env_float("TRAFFIC_ANOMALY_DEADBAND", cls.anomaly_deadband),
            anomaly_decay_minutes=env_float("TRAFFIC_ANOMALY_DECAY_MINUTES", cls.anomaly_decay_minutes),
            alert_cooldown_minutes=env_float("TRAFFIC_ALERT_COOLDOWN_MINUTES", cls.alert_cooldown_minutes),
        )
