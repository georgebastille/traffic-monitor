from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence, cast

from zoneinfo import ZoneInfo

import googlemaps
import requests

NTFY_TOPIC = "traffic_monitor"


class DistanceMatrixClient(Protocol):
    """Minimal googlemaps client surface needed by the monitor."""

    def distance_matrix(
        self,
        origin: str,
        destination: str,
        *,
        mode: str,
        departure_time: Any,
        traffic_model: str,
    ) -> Mapping[str, Any]:
        ...


class NotificationSender(Protocol):
    """Callable used to deliver notifications."""

    def __call__(self, url: str, *, data: bytes) -> Any:
        ...


@dataclass(frozen=True)
class TrafficSample:
    """Canonical record for a single distance-matrix query."""

    query_time: datetime
    departure_time: datetime
    origin: str
    destination: str
    clear_duration_mins: float
    traffic_duration_mins: float

    def to_dict(self) -> Mapping[str, Any]:
        """Serialize the sample to a JSON-ready mapping."""
        return {
            "query_time": self.query_time.isoformat(),
            "departure_time": self.departure_time.isoformat(),
            "origin": self.origin,
            "destination": self.destination,
            "clear_duration_mins": self.clear_duration_mins,
            "traffic_duration_mins": self.traffic_duration_mins,
        }


def append_sample(path: Path | str, sample: TrafficSample) -> None:
    """Append a JSON record for the provided sample."""
    output_path = Path(path)
    parent = output_path.parent
    if parent and not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{json.dumps(sample.to_dict())}\n")


class TrafficMonitor:
    """High-level interface for retrieving traffic metrics and sending alerts."""

    def __init__(
        self,
        client: DistanceMatrixClient,
        *,
        timezone: str = "Europe/London",
        notification_topic: str = NTFY_TOPIC,
        send_notification: NotificationSender | None = None,
    ) -> None:
        self._client = client
        self._tz = timezone
        self._tzinfo = ZoneInfo(timezone)
        self._notification_topic = notification_topic
        self._send_notification = send_notification or requests.post

    @classmethod
    def from_api_key(
        cls,
        api_key: str,
        *,
        timezone: str = "Europe/London",
        notification_topic: str = NTFY_TOPIC,
    ) -> "TrafficMonitor":
        """Factory for production use."""
        client = create_googlemaps_client(api_key)
        return cls(client, timezone=timezone, notification_topic=notification_topic)

    def get_traffic_data(
        self,
        origin: str,
        destination: str,
        *,
        departure_time: time | str | None = None,
    ) -> TrafficSample:
        """Fetch the clear/traffic travel times for the requested trip."""
        (departure_arg, departure_dt) = self._resolve_departure_time(departure_time)
        response = self._client.distance_matrix(
            origin,
            destination,
            mode="driving",
            departure_time=departure_arg,
            traffic_model="pessimistic",
        )
        return self._parse_distance_matrix_response(response, departure_dt)

    def notify(self, message: str) -> None:
        """Send a notification to the configured ntfy topic."""
        url = f"https://ntfy.sh/{self._notification_topic}"
        self._send_notification(url, data=message.encode())

    def _resolve_departure_time(self, departure_time: time | str | None) -> tuple[Any, datetime]:
        if departure_time is None:
            now = datetime.now(self._tzinfo)
            return "now", now
        next_departure = _next_departure(departure_time, tz=self._tzinfo)
        return next_departure.epoch_seconds, next_departure.timestamp

    def _parse_distance_matrix_response(
        self,
        response: Mapping[str, Any],
        departure_time: datetime,
    ) -> TrafficSample:
        origin_address, destination_address = _extract_addresses(response)
        element = _first_element(response)
        clear_duration_mins = _extract_duration(element, "duration")
        traffic_duration_mins = _extract_duration(element, "duration_in_traffic")
        return TrafficSample(
            query_time=datetime.now(self._tzinfo),
            departure_time=departure_time,
            origin=origin_address,
            destination=destination_address,
            clear_duration_mins=clear_duration_mins,
            traffic_duration_mins=traffic_duration_mins,
        )


def create_googlemaps_client(api_key: str) -> googlemaps.Client:
    """Create a Google Maps client suitable for dependency injection."""
    return googlemaps.Client(key=api_key)


@dataclass(frozen=True)
class DepartureInfo:
    epoch_seconds: int
    timestamp: datetime


def _next_departure(target_time: time | str, *, tz: ZoneInfo) -> DepartureInfo:
    """Return epoch seconds and timezone-aware datetime for the next occurrence of target_time."""
    parsed_time = time.fromisoformat(target_time) if isinstance(target_time, str) else target_time
    now = datetime.now(tz)
    today_target = now.replace(
        hour=parsed_time.hour,
        minute=parsed_time.minute,
        second=getattr(parsed_time, "second", 0),
        microsecond=0,
    )
    candidate = today_target if now <= today_target else today_target + timedelta(days=1)
    return DepartureInfo(epoch_seconds=int(candidate.timestamp()), timestamp=candidate)


def _extract_addresses(response: Mapping[str, Any]) -> tuple[str, str]:
    origin_addresses = response.get("origin_addresses") or []
    destination_addresses = response.get("destination_addresses") or []
    if not origin_addresses or not destination_addresses:
        raise ValueError("Distance matrix response missing addresses")
    return origin_addresses[0], destination_addresses[0]


def _first_element(response: Mapping[str, Any]) -> Mapping[str, Any]:
    rows = cast(Sequence[Mapping[str, Any]], response.get("rows") or [])
    if not rows:
        raise ValueError("Distance matrix response missing rows")
    row = rows[0]
    elements = cast(Sequence[Mapping[str, Any]], row.get("elements") or [])
    if not elements:
        raise ValueError("Distance matrix response missing elements")
    return elements[0]


def _extract_duration(element: Mapping[str, Any], key: str) -> float:
    node = element.get(key)
    if not node or "value" not in node:
        raise ValueError(f"Distance matrix element missing {key}")
    return float(node["value"]) / 60.0
