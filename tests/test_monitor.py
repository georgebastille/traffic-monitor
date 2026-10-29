from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import pytest

from traffic_monitor.monitor import TrafficMonitor, TrafficSample, append_sample
from traffic_monitor.plotting import plot_anomaly_to_png
from traffic_monitor.tomtom import RouteResult


class FakeClient:
    def __init__(self, results: list[RouteResult]) -> None:
        self._results = results
        self.calls: list[dict[str, Any]] = []

    def route(
        self,
        origin: str,
        destination: str,
        *,
        waypoints: Sequence[tuple[float, float]] = (),
        depart_at: datetime | None = None,
    ) -> RouteResult:
        self.calls.append(
            {"origin": origin, "destination": destination, "waypoints": list(waypoints), "depart_at": depart_at}
        )
        if not self._results:
            raise RuntimeError("No fake results remaining")
        return self._results.pop(0)


def test_get_traffic_data_returns_typed_sample(tmp_path: Path) -> None:
    client = FakeClient([RouteResult(traffic_secs=900, clear_secs=600, points=[])])
    monitor = TrafficMonitor(
        client, timezone="UTC", via_waypoints=[(51.45, -0.1)], route_cache_path=tmp_path / "r.json"
    )

    sample = monitor.get_traffic_data("Origin", "Destination")

    assert sample.origin == "Origin"
    assert sample.clear_duration_mins == pytest.approx(10.0)
    assert sample.traffic_duration_mins == pytest.approx(15.0)
    assert client.calls[0]["waypoints"] == [(51.45, -0.1)]
    assert client.calls[0]["depart_at"] == sample.departure_time


def test_waypoints_are_computed_and_cached(tmp_path: Path) -> None:
    baseline_path = tmp_path / "baseline.json"
    points = [(51.4500, -0.1000), (51.4520, -0.1050), (51.4540, -0.1100), (51.4560, -0.1150), (51.4580, -0.1200)]
    client = FakeClient(
        [
            RouteResult(traffic_secs=600, clear_secs=600, points=points),
            RouteResult(traffic_secs=900, clear_secs=600, points=[]),
        ]
    )
    monitor = TrafficMonitor(client, timezone="UTC", route_cache_path=baseline_path)

    sample = monitor.get_traffic_data("Origin", "Destination")

    payload = json.loads(baseline_path.read_text())
    assert payload["origin"] == "Origin"
    assert payload["destination"] == "Destination"
    assert sample.traffic_duration_mins == pytest.approx(15.0)
    assert client.calls[1]["waypoints"] == [(51.4520, -0.1050), (51.4540, -0.1100), (51.4560, -0.1150)]

    # A new monitor reuses the cached waypoints without recomputing them.
    reuse = FakeClient([RouteResult(traffic_secs=900, clear_secs=600, points=[])])
    TrafficMonitor(reuse, timezone="UTC", route_cache_path=baseline_path).get_traffic_data("Origin", "Destination")
    assert len(reuse.calls) == 1
    assert reuse.calls[0]["waypoints"] == client.calls[1]["waypoints"]


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

    result = plot_anomaly_to_png(jsonl_path, png_path)

    assert result == png_path
    assert png_path.exists()
