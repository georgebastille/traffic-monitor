from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

import googlemaps
import requests

NTFY_TOPIC = "traffic_monitor"


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
    ) -> None:
        self._client = client
        self._zone = ZoneInfo(timezone)
        self._topic = topic
        self._notifier = notifier or (lambda msg: requests.post(f"https://ntfy.sh/{self._topic}", data=msg.encode()))

    @classmethod
    def from_api_key(
        cls, api_key: str, *, timezone: str = "Europe/London", topic: str = NTFY_TOPIC
    ) -> "TrafficMonitor":
        return cls(googlemaps.Client(key=api_key), timezone=timezone, topic=topic)

    def get_traffic_data(
        self,
        origin: str,
        destination: str,
        *,
        departure_time: time | str | None = None,
    ) -> TrafficSample:
        departure_arg, departure_dt = self._resolve_departure(departure_time)
        response = self._client.distance_matrix(
            origin,
            destination,
            mode="driving",
            departure_time=departure_arg,
            traffic_model="pessimistic",
        )
        element = _first_element(response)
        return TrafficSample(
            query_time=datetime.now(self._zone),
            departure_time=departure_dt,
            origin=_first_value(response, "origin_addresses"),
            destination=_first_value(response, "destination_addresses"),
            clear_duration_mins=_duration_minutes(element, "duration"),
            traffic_duration_mins=_duration_minutes(element, "duration_in_traffic"),
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


def _first_value(response: Mapping[str, Any], key: str) -> str:
    values: Sequence[str] = response.get(key, [])  # type: ignore[assignment]
    if not values:
        raise ValueError(f"Distance matrix response missing {key}")
    return values[0]


def _first_element(response: Mapping[str, Any]) -> Mapping[str, Any]:
    rows: Sequence[Mapping[str, Any]] = response.get("rows", [])  # type: ignore[assignment]
    if not rows:
        raise ValueError("Distance matrix response missing rows")
    elements: Sequence[Mapping[str, Any]] = rows[0].get("elements", [])  # type: ignore[assignment]
    if not elements:
        raise ValueError("Distance matrix response missing elements")
    return elements[0]


def _duration_minutes(element: Mapping[str, Any], key: str) -> float:
    payload = element.get(key)
    if not payload or "value" not in payload:
        raise ValueError(f"Distance matrix element missing {key}")
    return float(payload["value"]) / 60.0
