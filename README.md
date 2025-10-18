## Traffic Monitor

This project polls drive-time estimates between predefined waypoints, plots the latest series, and sends departure/pattern notifications.

### Weekday EMA Baseline
- Travel-time baselines now use a 5-minute bucketed EMA built from the last five weekday observations in the same bucket.
- The EMA spans five days (`alpha = 2/(5+1)`) and skips weekends and the current day so the estimate reflects historical behaviour.

### Integral Anomaly Detection
- Alerts are driven by an integral of deviation vs. the EMA baseline; the integral decays with a 120-minute half-life so a series of smaller departures can still accumulate into an alert.
- Defaults (override via env vars):
  - `TRAFFIC_ANOMALY_THRESHOLD` — integral trigger, defaults to `180.0`.
  - `TRAFFIC_ANOMALY_DEADBAND` — deadband minutes excluded from the integral (defaults to `2.0` to ignore minor wobble).
  - `TRAFFIC_ANOMALY_DECAY_MINUTES` — exponential decay window (defaults to `120.0`).
- The monitor emits at most one anomaly notification per weekday; integrals reset after an alert.

### Data Retention
- `traffic_report.jsonl` is pruned to the most recent 14 days on each run to prevent unbounded growth.
- Daily backups for manual analysis live under `backups/` (created outside the automated run).

### Calibration Tips
- Replays of the 2025-10 data show large platform shifts producing integrals >300; normal morning noise remains below ~80.
- Thresholds around `180` with a `2.0` minute deadband caught the TomTom migration anomaly while avoiding noise; bump to `220` if you only want the largest shifts.
- Run `UV_CACHE_DIR=.uv-cache uv run python -m pytest` after adjustments to validate analytics/notification behaviour.
