## Traffic Monitor

Polls drive time for the active route (`data/routes.json`) every 5 minutes, sends a "leave by" notification
before the school run, and alerts on sustained changes in traffic at any time of day. A small FastAPI app
(`traffic_monitor/api.py`, run by `traffic-api.service`) serves the PWA and its `/api/summary` (verdict + chart data).

### Running
- `monitor.sh` runs `main.py` from cron every 5 minutes; `monitor.sh archive.py` runs the nightly archive job.
  Both share `flock /tmp/traffic.lock` so they never overlap.
- `uv run python -m pytest` runs the tests; `uv run ruff check .` lints.

### Baseline
- The baseline for a time of day pools every reading within ±10 minutes (`BASELINE_WINDOW_MINUTES`) of it on the
  last five weekdays (~25 readings). Weekends and today are excluded.
- Change alerts and the app's "typical" compare against the median (`ALERT_BASELINE_PERCENTILE`, 50); the
  leave-by time is built on a cautious P75 (`DEPARTURE_BASELINE_PERCENTILE`, 75). In a Mar–Oct 2026 replay the
  window halved mornings where leave-by was 4+ minutes too late (2.7% → 1.3%), and the median cut "traffic eased"
  alerts from 33 to 8 while catching more sustained slowdowns (27 → 43).

### Departure notification
- The trip duration at the recommended departure time is predicted as the current duration plus the P75 baseline
  change between now and the departure bucket.
- It fires from 30 minutes before the recommended departure, then again only if the level worsens or the leave
  time moves 3+ minutes earlier. Titles lead with the traffic light and leave time for watch notifications.

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
- The app's chart is drawn in the browser from `/api/summary`.
- `data/geocode_cache.json` and `data/<route>_baseline.json` (fixed via-waypoints) cache TomTom lookups.
