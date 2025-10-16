from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping, Sequence
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import requests
from googlemaps import convert


class TomTomClient:
    def __init__(
        self,
        api_key: str,
        *,
        session: requests.Session | None = None,
        timezone: str = "Europe/London",
    ) -> None:
        self._api_key = api_key
        self._session = session or requests.Session()
        self._timezone = ZoneInfo(timezone)
        self._geocode_cache: dict[str, tuple[float, float]] = {}

    def directions(
        self,
        origin: str,
        destination: str,
        *,
        mode: str = "driving",
        departure_time: object | None = None,
        traffic_model: str | None = None,
        alternatives: bool = False,
        waypoints: Sequence[str] | None = None,
    ) -> list[Mapping[str, object]]:
        origin_coords = self._geocode(origin)
        destination_coords = self._geocode(destination)
        waypoint_coords = self._parse_waypoints(waypoints or [])
        path = ":".join(_format_coords(coords) for coords in [origin_coords, *waypoint_coords, destination_coords])
        depart_at = self._resolve_departure_time(departure_time)
        params = {
            "key": self._api_key,
            "traffic": "true",
            "travelMode": self._translate_mode(mode),
            "computeBestOrder": "false",
            "departAt": depart_at.isoformat(timespec="seconds"),
        }
        response = self._session.get(
            f"https://api.tomtom.com/routing/1/calculateRoute/{path}/json",
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        routes: list[Mapping[str, object]] = payload.get("routes", [])  # type: ignore[assignment]
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
        leg = {
            "start_address": origin,
            "end_address": destination,
            "duration": {"value": float(clear_secs)},
            "duration_in_traffic": {"value": travel_secs},
        }
        polyline = self._encode_polyline(route.get("legs", []))
        route_payload: dict[str, object] = {"legs": [leg]}
        if polyline:
            route_payload["overview_polyline"] = {"points": polyline}
        return [route_payload]

    def _translate_mode(self, mode: str) -> str:
        if mode == "driving":
            return "car"
        if mode == "transit":
            return "bus"
        return mode

    def _resolve_departure_time(self, departure_time: object | None) -> datetime:
        if departure_time in (None, "now"):
            return datetime.now(self._timezone)
        if isinstance(departure_time, datetime):
            return departure_time.astimezone(self._timezone)
        if isinstance(departure_time, (int, float)):
            return datetime.fromtimestamp(departure_time, tz=self._timezone)
        raise ValueError(f"Unsupported departure_time value for TomTom client: {departure_time!r}")

    def _parse_waypoints(self, waypoints: Iterable[str]) -> list[tuple[float, float]]:
        coords: list[tuple[float, float]] = []
        for waypoint in waypoints:
            entry = waypoint[4:] if waypoint.startswith("via:") else waypoint
            try:
                lat_str, lng_str = entry.split(",", 1)
                coords.append((float(lat_str), float(lng_str)))
            except ValueError:
                continue
        return coords

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
        return coords

    def _encode_polyline(self, legs: Iterable[Mapping[str, object]]) -> str:
        points: list[tuple[float, float]] = []
        for leg in legs:
            for point in leg.get("points", []) or []:
                lat = point.get("latitude")
                lng = point.get("longitude")
                if lat is None or lng is None:
                    continue
                points.append((float(lat), float(lng)))
        if not points:
            return ""
        return convert.encode_polyline(points)


def _format_coords(coords: tuple[float, float]) -> str:
    lat, lng = coords
    return f"{lat:.6f},{lng:.6f}"
