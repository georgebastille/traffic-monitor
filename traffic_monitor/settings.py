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

    baseline_percentile: int = 75
    orange_mins: float = 2.0
    red_mins: float = 5.0
    anomaly_threshold: float = 90.0
    anomaly_deadband: float = 2.0
    anomaly_decay_minutes: float = 120.0
    alert_cooldown_minutes: float = 120.0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            baseline_percentile=int(env_float("BASELINE_PERCENTILE", cls.baseline_percentile)),
            orange_mins=env_float("TRAFFIC_ORANGE_MINUTES", cls.orange_mins),
            red_mins=env_float("TRAFFIC_RED_MINUTES", cls.red_mins),
            anomaly_threshold=env_float("TRAFFIC_ANOMALY_THRESHOLD", cls.anomaly_threshold),
            anomaly_deadband=env_float("TRAFFIC_ANOMALY_DEADBAND", cls.anomaly_deadband),
            anomaly_decay_minutes=env_float("TRAFFIC_ANOMALY_DECAY_MINUTES", cls.anomaly_decay_minutes),
            alert_cooldown_minutes=env_float("TRAFFIC_ALERT_COOLDOWN_MINUTES", cls.alert_cooldown_minutes),
        )
