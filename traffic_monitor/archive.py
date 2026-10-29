from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Iterable


def archive_samples(sources: Iterable[Path | str], archive_dir: Path | str) -> int:
    """
    Merge JSONL samples into monthly archive files (``archive_dir/YYYY-MM.jsonl``).

    Rows are keyed on their raw ``query_time`` string, so re-running over the same
    source is a no-op. Only months that gain rows are rewritten. Returns the number
    of rows added.
    """
    target_dir = Path(archive_dir)
    incoming: dict[str, dict[str, str]] = {}
    for source in sources:
        for key, month, line in _read_rows(Path(source)):
            incoming.setdefault(month, {})[key] = line

    added = 0
    for month, rows in sorted(incoming.items()):
        month_path = target_dir / f"{month}.jsonl"
        existing = {key: line for key, _, line in _read_rows(month_path)}
        new_keys = rows.keys() - existing.keys()
        if not new_keys:
            continue
        merged = existing | {key: rows[key] for key in new_keys}
        _write_atomic(month_path, (merged[key] for key in sorted(merged, key=_sort_key)))
        added += len(new_keys)
    return added


def _read_rows(path: Path) -> Iterable[tuple[str, str, str]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                query_time = str(json.loads(stripped)["query_time"])
                moment = datetime.fromisoformat(query_time)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
            yield query_time, moment.strftime("%Y-%m"), stripped


def _sort_key(query_time: str) -> datetime:
    moment = datetime.fromisoformat(query_time)
    return moment if moment.tzinfo else moment.astimezone()


def _write_atomic(path: Path, lines: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for line in lines:
            handle.write(f"{line}\n")
    os.replace(tmp, path)
