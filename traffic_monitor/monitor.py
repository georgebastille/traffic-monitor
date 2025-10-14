from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

import googlemaps
import requests
from googlemaps import convert

NTFY_TOPIC = "traffic_monitor"
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
        client: Any,
        *,
        timezone: str = "Europe/London",
        notifier: Callable[[str], None] | None = None,
        topic: str = NTFY_TOPIC,
        via_waypoints: Sequence[tuple[float, float]] | None = None,
        route_cache_path: Path | str = ROUTE_BASELINE_PATH,
    ) -> None:
        self._client = client
        self._zone = ZoneInfo(timezone)
        self._topic = topic
        self._notifier = notifier or (lambda msg: requests.post(f"https://ntfy.sh/{self._topic}", data=msg.encode()))
        self._via_waypoints: list[tuple[float, float]] | None = list(via_waypoints) if via_waypoints else None
        self._route_cache_path = Path(route_cache_path)

    @classmethod
    def from_api_key(
        cls,
        api_key: str,
        *,
        timezone: str = "Europe/London",
        topic: str = NTFY_TOPIC,
        via_waypoints: Sequence[tuple[float, float]] | None = None,
        route_cache_path: Path | str = ROUTE_BASELINE_PATH,
    ) -> "TrafficMonitor":
        return cls(
            googlemaps.Client(key=api_key),
            timezone=timezone,
            topic=topic,
            via_waypoints=via_waypoints,
            route_cache_path=route_cache_path,
        )

    def get_traffic_data(
        self,
        origin: str,
        destination: str,
        *,
        departure_time: time | str | None = None,
    ) -> TrafficSample:
        departure_arg, departure_dt = self._resolve_departure(departure_time)
        waypoints = self._resolve_waypoints(origin, destination)
        response = self._client.directions(
            origin,
            destination,
            mode="driving",
            departure_time=departure_arg,
            traffic_model="pessimistic",
            alternatives=False,
            waypoints=waypoints if waypoints else None,
        )
        route = _first_route(response)
        leg = _first_leg(route)
        return TrafficSample(
            query_time=datetime.now(self._zone),
            departure_time=departure_dt,
            origin=leg.get("start_address", origin),
            destination=leg.get("end_address", destination),
            clear_duration_mins=_sum_duration_minutes(route, "duration"),
            traffic_duration_mins=_sum_duration_minutes(route, "duration_in_traffic"),
        )

    def notify(self, message: str) -> None:
        self._notifier(message)

    def _resolve_departure(self, departure_time: time | str | None) -> tuple[Any, datetime]:
        if departure_time is None:
            now = datetime.now(self._zone)
            return "now", now
        target = time.fromisoformat(departure_time) if isinstance(departure_time, str) else departure_time
        now = datetime.now(self._zone)
        today = now.replace(hour=target.hour, minute=target.minute, second=getattr(target, "second", 0), microsecond=0)
        scheduled = today if now <= today else today + timedelta(days=1)
        return int(scheduled.timestamp()), scheduled

    def _resolve_waypoints(self, origin: str, destination: str) -> list[str]:
        if self._via_waypoints is None:
            self._via_waypoints = self._load_cached_waypoints(origin, destination) or self._compute_waypoints(
                origin, destination
            )
        return [f"via:{lat:.6f},{lng:.6f}" for lat, lng in self._via_waypoints]

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
        response = self._client.directions(
            origin,
            destination,
            mode="driving",
            alternatives=False,
        )
        route = _first_route(response)
        polyline = route.get("overview_polyline", {})
        encoded = polyline.get("points")
        if not encoded:
            raise ValueError("Directions response missing overview polyline for anchor calculation")
        points = convert.decode_polyline(encoded)
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


def _first_route(response: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    if not response:
        raise ValueError("Directions response missing routes")
    return response[0]


def _first_leg(route: Mapping[str, Any]) -> Mapping[str, Any]:
    legs: Sequence[Mapping[str, Any]] = route.get("legs", [])  # type: ignore[assignment]
    if not legs:
        raise ValueError("Directions route missing legs")
    return legs[0]


def _sum_duration_minutes(route: Mapping[str, Any], key: str) -> float:
    legs: Sequence[Mapping[str, Any]] = route.get("legs", [])  # type: ignore[assignment]
    if not legs:
        raise ValueError("Directions route missing legs")
    total_seconds = 0.0
    for leg in legs:
        payload = leg.get(key) or leg.get("duration")
        if not payload or "value" not in payload:
            raise ValueError(f"Directions leg missing {key}")
        total_seconds += float(payload["value"])
    return total_seconds / 60.0


def _sample_waypoints(points: Iterable[Mapping[str, float]], count: int) -> list[tuple[float, float]]:
    filtered = [(float(pt["lat"]), float(pt["lng"])) for pt in points]
    if len(filtered) <= 2:
        return filtered[1:-1] if len(filtered) > 2 else []
    if count <= 0:
        return []
    step = max(len(filtered) // (count + 1), 1)
    middle = filtered[1:-1]
    sampled = middle[::step][:count]
    return sampled
