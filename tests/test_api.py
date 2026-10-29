from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from traffic_monitor.api import app
from traffic_monitor.monitor import TrafficSample, append_sample


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("API_TOKEN", "secret")
    route = {
        "id": "school-run",
        "name": "School Run",
        "origin": "Origin",
        "destination": "Destination",
        "arrival_time": "08:20",
        "timezone": "Europe/London",
        "provider": "tomtom",
        "active": True,
    }
    (tmp_path / "routes.json").write_text(json.dumps({"routes": [route]}), encoding="utf-8")
    start = datetime(2026, 10, 27, 8, 0, tzinfo=timezone.utc)
    for offset in range(3):
        moment = start + timedelta(days=offset)
        append_sample(tmp_path / "school-run_traffic.jsonl", TrafficSample(moment, moment, "O", "D", 9.0, 12.0))
    return tmp_path


def test_chart_renders_on_demand_and_reuses_fresh_png(data_dir: Path) -> None:
    client = TestClient(app)
    png = data_dir / "school-run_anomaly.png"

    response = client.get("/api/chart/school-run", params={"token": "secret"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert png.exists()

    os.utime(png, (png.stat().st_mtime + 60,) * 2)
    rendered_at = png.stat().st_mtime
    assert client.get("/api/chart/school-run", params={"token": "secret"}).status_code == 200
    assert png.stat().st_mtime == rendered_at


def test_chart_rejects_unknown_route(data_dir: Path) -> None:
    client = TestClient(app)
    response = client.get("/api/chart/..%2Fsecrets", params={"token": "secret"})
    assert response.status_code == 404
