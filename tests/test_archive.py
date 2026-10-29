from __future__ import annotations

import json
from pathlib import Path

from traffic_monitor.archive import archive_samples


def _row(query_time: str, duration: float = 10.0) -> str:
    return json.dumps({"query_time": query_time, "traffic_duration_mins": duration})


def _write(path: Path, rows: list[str]) -> Path:
    path.write_text("".join(f"{row}\n" for row in rows), encoding="utf-8")
    return path


def test_archive_samples_splits_by_month(tmp_path: Path) -> None:
    source = _write(
        tmp_path / "hot.jsonl",
        [_row("2026-09-30T23:55:00+01:00"), _row("2026-10-01T00:00:00+01:00"), "not json", ""],
    )
    archive_dir = tmp_path / "archive"

    added = archive_samples([source], archive_dir)

    assert added == 2
    assert sorted(p.name for p in archive_dir.iterdir()) == ["2026-09.jsonl", "2026-10.jsonl"]


def test_archive_samples_is_idempotent_and_merges_sorted(tmp_path: Path) -> None:
    archive_dir = tmp_path / "archive"
    first = _write(tmp_path / "a.jsonl", [_row("2026-10-02T08:00:00+01:00"), _row("2026-10-03T08:00:00+01:00")])
    second = _write(tmp_path / "b.jsonl", [_row("2026-10-01T08:00:00+01:00"), _row("2026-10-03T08:00:00+01:00")])

    assert archive_samples([first], archive_dir) == 2
    assert archive_samples([first], archive_dir) == 0
    assert archive_samples([second], archive_dir) == 1

    lines = (archive_dir / "2026-10.jsonl").read_text(encoding="utf-8").splitlines()
    times = [json.loads(line)["query_time"] for line in lines]
    assert times == ["2026-10-01T08:00:00+01:00", "2026-10-02T08:00:00+01:00", "2026-10-03T08:00:00+01:00"]
