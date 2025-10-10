# Repository Guidelines

## Project Structure & Module Organization
- `main.py` orchestrates data fetches, plotting, and notifications; factor reusable helpers into a package once the module grows.
- `monitor.sh` is the cron entry point that runs `uv` and appends logs to `cron.log`.
- Generated artifacts (`traffic_report*.jsonl` / `.png`) live in the project root; treat them as reproducible outputs rather than hand-edited files.
- `notebooks/` contains exploratory analyses and sample exports—keep large intermediates out of version control.
- `pyproject.toml` and `uv.lock` define the Python 3.13 toolchain; update dependencies via `uv` so the lock stays in sync.

## Build, Test, and Development Commands
- `uv sync --all-extras` provisions the environment defined in `pyproject.toml`.
- `uv run main.py` executes the monitor once, refreshing JSONL logs and charts.
- `uv run python -m pytest` (when tests exist) runs the suite.
- `uv run ruff check .` lints; `uv run ruff format .` applies the formatter.

## Coding Style & Naming Conventions
- Target Python 3.13, 4-space indentation, 120-character lines, double quotes—Ruff enforces Black-like defaults.
- Use descriptive `lower_snake_case` for functions and variables, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants.
- Keep modules small and purpose-driven; migrate cross-cutting logic into a dedicated package (e.g., `traffic_monitor/`) as the codebase expands.

## Testing Guidelines
- Add `pytest` cases under `tests/`, mirroring module names (`test_main.py`, etc.).
- Mock external services (Google Maps, ntfy) to keep tests deterministic; assert JSONL schema and anomaly calculations.
- Aim to cover success, failure, and alerting paths; introduce regression tests when bugs are fixed.

## Commit & Pull Request Guidelines
- Commits use short, imperative subjects (see `Traffic Alert Notification` in the log) and scope a single concern.
- Document intent, context, and follow-ups in the commit body when needed.
- Pull requests should describe behavioural changes, include manual verification steps (`uv run main.py`), attach updated plots when visuals change, and link issues.
- Run linting/tests before requesting review; refresh or ignore generated artifacts consciously.

## Secrets & Configuration
- Store credentials in an untracked `.env`; set `GOOGLE_MAPS_API_KEY` before running.
- Update `monitor.sh` if runtime paths change and keep cron-friendly logging targeted at `cron.log`.
