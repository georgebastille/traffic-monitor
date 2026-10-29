## Traffic Monitor

Polls drive time for the active route (`data/routes.json`) every 5 minutes, sends a "leave by" notification
before the school run, and alerts on sustained changes in traffic at any time of day. A small FastAPI app
(`traffic_monitor/api.py`, run by `traffic-api.service`) serves the PWA, status, history and chart.

### Running
- `monitor.sh` runs `main.py` from cron every 5 minutes; `monitor.sh archive.py` runs the nightly archive job.
  Both share `flock /tmp/traffic.lock` so they never overlap.
- `uv run python -m pytest` runs the tests; `uv run ruff check .` lints.

### Baseline
- For each 5-minute time-of-day bucket, the baseline is the P75 (`BASELINE_PERCENTILE`) of the same bucket over
  the last five weekdays. Weekends and today are excluded. `BASELINE_PERCENTILE=50` switches to an EMA of daily
  means.

### Departure notification
- The trip duration at the recommended departure time is predicted as the current duration plus the baseline
  change between now and the departure bucket. In a backtest over Aug–Oct 2026 this cut mornings where the
  advice was 4+ minutes too late from 22% to 2.5%.
- It fires from 30 minutes before the recommended departure, and again if the departure time moves earlier.

### Traffic alerts
- Integrates deviation from the baseline beyond a deadband, with exponential decay, and fires when the integral
  crosses a threshold; a cooldown stops repeats. The integral resets each day. Runs on weekdays, 24/7.
- Settings (env vars): `TRAFFIC_ANOMALY_THRESHOLD` (90 minute-minutes), `TRAFFIC_ANOMALY_DEADBAND` (2.0 mins),
  `TRAFFIC_ANOMALY_DECAY_MINUTES` (120), `TRAFFIC_ALERT_COOLDOWN_MINUTES` (120).

### Data
- `data/<route>_traffic.jsonl` is the hot file the 5-minute run reads. The nightly `archive.py` merges it into
  `data/archive/<route>/YYYY-MM.jsonl` (idempotent) and only then trims it to `HISTORY_DAYS` (90).
- The archive is never pruned. Query it offline, e.g. `duckdb -c "select * from 'data/archive/*/*.jsonl'"`.
- Backfill other files with `uv run archive.py --route <id> --source file.jsonl`.
- The chart PNG is rendered by the API on request, only when there are new samples.
- `data/geocode_cache.json` and `data/<route>_baseline.json` (fixed via-waypoints) cache TomTom lookups.
