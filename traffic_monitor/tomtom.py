from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import requests


@dataclass(frozen=True)
class RouteResult:
    traffic_secs: float
    clear_secs: float
    points: list[tuple[float, float]]


class TomTomClient:
    def __init__(
        self,
        api_key: str,
        *,
        session: requests.Session | None = None,
        timezone: str = "Europe/London",
        geocode_cache_path: Path | str | None = None,
    ) -> None:
        self._api_key = api_key
        self._session = session or requests.Session()
        self._timezone = ZoneInfo(timezone)
        self._geocode_cache_path = Path(geocode_cache_path) if geocode_cache_path else None
        self._geocode_cache = self._load_geocode_cache()

    def route(
        self,
        origin: str,
        destination: str,
        *,
        waypoints: Sequence[tuple[float, float]] = (),
        depart_at: datetime | None = None,
    ) -> RouteResult:
        """Car route through ``waypoints`` (lat, lng) with live traffic."""
        origin_coords = self._geocode(origin)
        destination_coords = self._geocode(destination)
        path = ":".join(_format_coords(coords) for coords in [origin_coords, *waypoints, destination_coords])
        depart = depart_at or datetime.now(self._timezone)
        params = {
            "key": self._api_key,
            "traffic": "true",
            "travelMode": "car",
            "computeBestOrder": "false",
            "departAt": depart.isoformat(timespec="seconds"),
        }
        response = self._session.get(
            f"https://api.tomtom.com/routing/1/calculateRoute/{path}/json",
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        routes: list[Mapping[str, Any]] = payload.get("routes", [])
        if not routes:
            raise ValueError("TomTom response missing routes")
        route = routes[0]
        summary = route.get("summary", {})
        travel_secs = float(summary.get("travelTimeInSeconds", 0.0))
        if travel_secs <= 0:
            raise ValueError("TomTom route missing travel time")
        clear_secs = summary.get("noTrafficTravelTimeInSeconds")
        if clear_secs is None:
            delay = float(summary.get("trafficDelayInSeconds", 0.0))
            clear_secs = max(travel_secs - delay, 0.0)
        points = [
            (float(point["latitude"]), float(point["longitude"]))
            for leg in route.get("legs", [])
            for point in leg.get("points", []) or []
            if point.get("latitude") is not None and point.get("longitude") is not None
        ]
        return RouteResult(traffic_secs=travel_secs, clear_secs=float(clear_secs), points=points)

    def _geocode(self, address: str) -> tuple[float, float]:
        if address in self._geocode_cache:
            return self._geocode_cache[address]
        params = {
            "key": self._api_key,
            "limit": 1,
        }
        response = self._session.get(
            f"https://api.tomtom.com/search/2/geocode/{quote_plus(address)}.json",
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        results: list[Mapping[str, object]] = payload.get("results", [])  # type: ignore[assignment]
        if not results:
            raise ValueError(f"TomTom geocode returned no results for address {address!r}")
        position = results[0].get("position", {})
        lat = position.get("lat") or position.get("latitude")
        lon = position.get("lon") or position.get("lng") or position.get("longitude")
        if lat is None or lon is None:
            raise ValueError(f"TomTom geocode missing coordinates for address {address!r}")
        coords = (float(lat), float(lon))
        self._geocode_cache[address] = coords
        if self._geocode_cache_path:
            self._geocode_cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {key: list(value) for key, value in self._geocode_cache.items()}
            self._geocode_cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return coords

    def _load_geocode_cache(self) -> dict[str, tuple[float, float]]:
        if not self._geocode_cache_path or not self._geocode_cache_path.exists():
            return {}
        try:
            payload = json.loads(self._geocode_cache_path.read_text(encoding="utf-8"))
            return {str(address): (float(lat), float(lon)) for address, (lat, lon) in payload.items()}
        except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
            return {}


def _format_coords(coords: tuple[float, float]) -> str:
    lat, lng = coords
    return f"{lat:.6f},{lng:.6f}"
