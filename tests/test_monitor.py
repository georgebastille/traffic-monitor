from __future__ import annotations

import json
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from traffic_monitor.monitor import TrafficMonitor, TrafficSample, append_sample
from traffic_monitor.plotting import plot_anomaly_to_png, plot_to_png


class FakeClient:
    def __init__(self, responses: List[Dict[str, Any]]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    def directions(
        self,
        origin: str,
        destination: str,
        *,
        mode: str,
        alternatives: bool,
        departure_time: Optional[Any] = None,
        traffic_model: Optional[str] = None,
        waypoints: Optional[List[str]] = None,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            {
                "origin": origin,
                "destination": destination,
                "mode": mode,
                "departure_time": departure_time,
                "traffic_model": traffic_model,
                "waypoints": waypoints,
                "alternatives": alternatives,
            }
        )
        if not self._responses:
            raise RuntimeError("No fake responses remaining")
        return self._responses.pop(0)


def build_directions_response(
    *,
    clear_duration_secs: int = 600,
    traffic_duration_secs: int = 900,
) -> list[dict[str, object]]:
    return [
        {
            "legs": [
                {
                    "start_address": "Origin Address",
                    "end_address": "Destination Address",
                    "duration": {"value": clear_duration_secs},
                    "duration_in_traffic": {"value": traffic_duration_secs},
                }
            ]
        }
    ]


def test_get_traffic_data_returns_typed_sample() -> None:
    client = FakeClient([build_directions_response()])
    monitor = TrafficMonitor(
        client,
        timezone="UTC",
        via_waypoints=[(51.0, -0.1), (51.1, -0.2)],
    )

    sample = monitor.get_traffic_data("A", "B")

    assert sample.origin == "Origin Address"
    assert sample.destination == "Destination Address"
    assert sample.clear_duration_mins == pytest.approx(10.0)
    assert sample.traffic_duration_mins == pytest.approx(15.0)
    assert sample.query_time.tzinfo is not None
    assert sample.departure_time.tzinfo is not None
    assert client.calls[0]["departure_time"] == "now"
    assert client.calls[0]["waypoints"] == ["via:51.000000,-0.100000", "via:51.100000,-0.200000"]


def test_scheduled_departure_uses_epoch_seconds() -> None:
    client = FakeClient([build_directions_response()])
    monitor = TrafficMonitor(client, timezone="UTC", via_waypoints=[(51.5, -0.15)])

    sample = monitor.get_traffic_data("A", "B", departure_time=time(8, 0))

    departure_arg = client.calls[0]["departure_time"]
    assert isinstance(departure_arg, int)
    assert 0 < departure_arg < 2**31
    assert sample.departure_time.hour == 8


def test_append_sample_writes_json(tmp_path: Path) -> None:
    sample = TrafficSample(
        query_time=datetime(2024, 10, 9, 7, 30, tzinfo=timezone.utc),
        departure_time=datetime(2024, 10, 9, 7, 30, tzinfo=timezone.utc),
        origin="Origin",
        destination="Destination",
        clear_duration_mins=12.5,
        traffic_duration_mins=18.0,
    )

    output = tmp_path / "sample.jsonl"
    append_sample(output, sample)

    content = output.read_text().strip().splitlines()
    assert len(content) == 1
    record = json.loads(content[0])
    assert record["clear_duration_mins"] == pytest.approx(12.5)
    assert record["origin"] == "Origin"


def test_plot_to_png_creates_chart(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "traffic.jsonl"
    png_path = tmp_path / "traffic.png"
    samples = [
        TrafficSample(
            query_time=datetime(2024, 10, 8, 7, 0, tzinfo=timezone.utc),
            departure_time=datetime(2024, 10, 8, 7, 0, tzinfo=timezone.utc),
            origin="Origin",
            destination="Destination",
            clear_duration_mins=12.0,
            traffic_duration_mins=18.0,
        ),
        TrafficSample(
            query_time=datetime(2024, 10, 9, 7, 5, tzinfo=timezone.utc),
            departure_time=datetime(2024, 10, 9, 7, 5, tzinfo=timezone.utc),
            origin="Origin",
            destination="Destination",
            clear_duration_mins=11.0,
            traffic_duration_mins=17.5,
        ),
    ]
    for sample in samples:
        append_sample(jsonl_path, sample)

    result = plot_to_png(jsonl_path, png_path)

    assert result == png_path
    assert png_path.exists()


def test_plot_anomaly_to_png_creates_chart(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "arrival.jsonl"
    png_path = tmp_path / "arrival.png"
    history_samples = [
        TrafficSample(
            query_time=datetime(2024, 10, 8, 8, 0, tzinfo=timezone.utc),
            departure_time=datetime(2024, 10, 8, 8, 0, tzinfo=timezone.utc),
            origin="Origin",
            destination="Destination",
            clear_duration_mins=12.0,
            traffic_duration_mins=18.0,
        ),
        TrafficSample(
            query_time=datetime(2024, 10, 8, 8, 5, tzinfo=timezone.utc),
            departure_time=datetime(2024, 10, 8, 8, 5, tzinfo=timezone.utc),
            origin="Origin",
            destination="Destination",
            clear_duration_mins=11.5,
            traffic_duration_mins=17.0,
        ),
    ]
    today_samples = [
        TrafficSample(
            query_time=datetime(2024, 10, 9, 8, 0, tzinfo=timezone.utc),
            departure_time=datetime(2024, 10, 9, 8, 0, tzinfo=timezone.utc),
            origin="Origin",
            destination="Destination",
            clear_duration_mins=12.0,
            traffic_duration_mins=22.0,
        ),
        TrafficSample(
            query_time=datetime(2024, 10, 9, 8, 5, tzinfo=timezone.utc),
            departure_time=datetime(2024, 10, 9, 8, 5, tzinfo=timezone.utc),
            origin="Origin",
            destination="Destination",
            clear_duration_mins=11.5,
            traffic_duration_mins=20.0,
        ),
    ]
    for sample in [*history_samples, *today_samples]:
        append_sample(jsonl_path, sample)

    result = plot_anomaly_to_png(jsonl_path, png_path)

    assert result == png_path
    assert png_path.exists()


def test_notify_uses_injected_sender() -> None:
    client = FakeClient([])
    captured: list[str] = []

    def fake_sender(message: str) -> None:
        captured.append(message)

    monitor = TrafficMonitor(client, timezone="UTC", topic="custom-topic", notifier=fake_sender)
    monitor.notify("hello world")
    assert captured == ["hello world"]


def test_plotting_handles_mixed_timezone_strings(tmp_path: Path) -> None:
    jsonl_path = tmp_path / "mixed.jsonl"
    png_path = tmp_path / "mixed.png"
    records = [
        {
            "query_time": "2024-10-08T08:00:00",
            "departure_time": "2024-10-08T08:00:00",
            "origin": "Origin",
            "destination": "Destination",
            "clear_duration_mins": 10.0,
            "traffic_duration_mins": 15.0,
        },
        {
            "query_time": "2024-10-08T08:05:00+01:00",
            "departure_time": "2024-10-08T08:05:00+01:00",
            "origin": "Origin",
            "destination": "Destination",
            "clear_duration_mins": 11.0,
            "traffic_duration_mins": 18.0,
        },
    ]
    jsonl_path.write_text("\n".join(json.dumps(record) for record in records) + "\n")

    result = plot_to_png(jsonl_path, png_path)

    assert result == png_path
    assert png_path.exists()


def test_waypoints_are_computed_and_cached(tmp_path: Path) -> None:
    from googlemaps import convert as gm_convert

    baseline_path = tmp_path / "baseline.json"
    # Polyline with 5 points
    encoded = gm_convert.encode_polyline(
        [
            (51.4500, -0.1000),
            (51.4520, -0.1050),
            (51.4540, -0.1100),
            (51.4560, -0.1150),
            (51.4580, -0.1200),
        ]
    )
    baseline_response = [
        {
            "overview_polyline": {"points": encoded},
            "legs": [
                {
                    "start_address": "Origin Address",
                    "end_address": "Destination Address",
                    "duration": {"value": 600},
                    "duration_in_traffic": {"value": 900},
                }
            ],
        }
    ]
    live_response = build_directions_response()
    client = FakeClient([baseline_response, live_response])
    monitor = TrafficMonitor(client, timezone="UTC", route_cache_path=baseline_path)

    sample = monitor.get_traffic_data("Origin", "Destination")

    assert baseline_path.exists()
    payload = json.loads(baseline_path.read_text())
    assert payload["origin"] == "Origin"
    assert payload["destination"] == "Destination"
    assert sample.traffic_duration_mins == pytest.approx(15.0)
    # First call used to build baseline (no departure time)
    assert client.calls[0]["departure_time"] is None
    assert client.calls[1]["waypoints"] == [
        "via:51.452000,-0.105000",
        "via:51.454000,-0.110000",
        "via:51.456000,-0.115000",
    ]
