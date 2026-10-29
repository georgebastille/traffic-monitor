from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

from traffic_monitor.tomtom import TomTomClient

DEFAULT_WAYPOINT_COUNT = 3
ROUTE_BASELINE_PATH = Path("traffic_route_baseline.json")


@dataclass(frozen=True)
class TrafficSample:
    query_time: datetime
    departure_time: datetime
    origin: str
    destination: str
    clear_duration_mins: float
    traffic_duration_mins: float

    def to_json_line(self) -> str:
        return json.dumps(
            {
                "query_time": self.query_time.isoformat(),
                "departure_time": self.departure_time.isoformat(),
                "origin": self.origin,
                "destination": self.destination,
                "clear_duration_mins": self.clear_duration_mins,
                "traffic_duration_mins": self.traffic_duration_mins,
            }
        )


def append_sample(path: Path | str, sample: TrafficSample) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{sample.to_json_line()}\n")


class TrafficMonitor:
    def __init__(
        self,
        client: TomTomClient,
        *,
        timezone: str = "Europe/London",
        via_waypoints: Sequence[tuple[float, float]] | None = None,
        route_cache_path: Path | str = ROUTE_BASELINE_PATH,
    ) -> None:
        self._client = client
        self._zone = ZoneInfo(timezone)
        self._via_waypoints: list[tuple[float, float]] | None = list(via_waypoints) if via_waypoints else None
        self._route_cache_path = Path(route_cache_path)

    def get_traffic_data(self, origin: str, destination: str) -> TrafficSample:
        waypoints = self._resolve_waypoints(origin, destination)
        departure = datetime.now(self._zone)
        result = self._client.route(origin, destination, waypoints=waypoints, depart_at=departure)
        return TrafficSample(
            query_time=datetime.now(self._zone),
            departure_time=departure,
            origin=origin,
            destination=destination,
            clear_duration_mins=result.clear_secs / 60.0,
            traffic_duration_mins=result.traffic_secs / 60.0,
        )

    def _resolve_waypoints(self, origin: str, destination: str) -> list[tuple[float, float]]:
        if self._via_waypoints is None:
            self._via_waypoints = self._load_cached_waypoints(origin, destination) or self._compute_waypoints(
                origin, destination
            )
        return self._via_waypoints

    def _load_cached_waypoints(self, origin: str, destination: str) -> list[tuple[float, float]] | None:
        if not self._route_cache_path.exists():
            return None
        try:
            payload = json.loads(self._route_cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        if payload.get("origin") != origin or payload.get("destination") != destination:
            return None
        waypoints = payload.get("waypoints")
        if not isinstance(waypoints, list):
            return None
        cleaned: list[tuple[float, float]] = []
        for item in waypoints:
            if (
                isinstance(item, Mapping)
                and "lat" in item
                and "lng" in item
                and isinstance(item["lat"], (int, float))
                and isinstance(item["lng"], (int, float))
            ):
                cleaned.append((float(item["lat"]), float(item["lng"])))
        return cleaned or None

    def _compute_waypoints(self, origin: str, destination: str) -> list[tuple[float, float]]:
        points = self._client.route(origin, destination).points
        if not points:
            raise ValueError("Route response missing points for anchor calculation")
        sampled = _sample_waypoints(points, DEFAULT_WAYPOINT_COUNT)
        record = {
            "origin": origin,
            "destination": destination,
            "waypoints": [{"lat": lat, "lng": lng} for lat, lng in sampled],
            "generated_at": datetime.now(self._zone).isoformat(),
        }
        self._route_cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._route_cache_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        return sampled


def _sample_waypoints(points: Sequence[tuple[float, float]], count: int) -> list[tuple[float, float]]:
    filtered = list(points)
    if len(filtered) <= 2:
        return filtered[1:-1] if len(filtered) > 2 else []
    if count <= 0:
        return []
    step = max(len(filtered) // (count + 1), 1)
    middle = filtered[1:-1]
    sampled = middle[::step][:count]
    return sampled
