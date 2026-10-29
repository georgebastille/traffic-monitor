"""
Nightly maintenance: copy each route's hot JSONL into its monthly archive, then trim
the hot file to HISTORY_DAYS. Run under the same flock as main.py.

Backfill extra files into a route's archive with:
    uv run archive.py --route school-run --source old.jsonl [--source ...]
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from traffic_monitor.analytics import prune_jsonl_history
from traffic_monitor.archive import archive_samples
from traffic_monitor.config import AppConfig


def log(message: str) -> None:
    print(f"[{datetime.now().isoformat()}] {message}")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--route", help="Route id to backfill (requires --source)")
    parser.add_argument("--source", action="append", default=[], type=Path, help="Extra JSONL file to archive")
    args = parser.parse_args()

    data_dir = Path(os.getenv("DATA_DIR", "."))
    archive_root = data_dir / "archive"

    if args.source:
        if not args.route:
            parser.error("--source requires --route")
        added = archive_samples(args.source, archive_root / args.route)
        log(f"Backfilled {added} rows into {archive_root / args.route}")
        return

    history_days = int(os.getenv("HISTORY_DAYS", "90"))
    cutoff = datetime.now(timezone.utc) - timedelta(days=history_days)
    for route in AppConfig.load(data_dir / "routes.json").routes:
        hot = data_dir / f"{route.id}_traffic.jsonl"
        if not hot.exists():
            continue
        added = archive_samples([hot], archive_root / route.id)
        removed = prune_jsonl_history(hot, cutoff=cutoff)
        log(f"{route.id}: archived {added} new rows, pruned {removed} rows older than {cutoff.date()}")


if __name__ == "__main__":
    main()
