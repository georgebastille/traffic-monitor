from __future__ import annotations

import json
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


def test_routes_ignore_legacy_provider_field(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_TOKEN", "secret")
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret"}
    body = {
        "id": "gym",
        "name": "Gym",
        "origin": "A",
        "destination": "B",
        "arrival_time": "09:00",
        "provider": "google",
    }

    assert client.post("/api/routes", json=body, headers=headers).status_code == 201
    routes = client.get("/api/routes", headers=headers).json()["routes"]
    assert [r["id"] for r in routes] == ["school-run", "gym"]
    assert all("provider" not in r for r in routes)


def test_summary_reports_verdict_and_chart(data_dir: Path) -> None:
    client = TestClient(app)

    response = client.get("/api/summary", headers={"Authorization": "Bearer secret"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["route_id"] == "school-run"
    assert payload["now"]["mins"] == 12.0
    assert payload["now"]["level"] in {"green", "orange", "red"}
    assert set(payload["departure"]) == {"active", "leave_by", "arrive_by", "mins", "typical_mins", "level"}
    chart = payload["chart"]
    assert len(chart["minutes"]) == len(chart["today"]) == len(chart["typical"]) == 288


def test_summary_requires_token(data_dir: Path) -> None:
    assert TestClient(app).get("/api/summary").status_code in {401, 403}
