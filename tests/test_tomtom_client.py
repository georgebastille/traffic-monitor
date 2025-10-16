from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from traffic_monitor.tomtom import TomTomClient


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError("HTTP error")

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeSession:
    def __init__(self) -> None:
        self._calls: list[tuple[str, dict[str, Any] | None]] = []

    def get(self, url: str, params: dict[str, Any] | None = None, timeout: int | None = None) -> FakeResponse:
        self._calls.append((url, params))
        if "search" in url:
            query = url.split("/geocode/", 1)[1].split(".json", 1)[0]
            if "Origin" in query:
                return FakeResponse({"results": [{"position": {"lat": 51.0, "lon": -0.1}}]})
            return FakeResponse({"results": [{"position": {"lat": 51.5, "lon": -0.2}}]})
        return FakeResponse(
            {
                "routes": [
                    {
                        "summary": {
                            "travelTimeInSeconds": 1800,
                            "noTrafficTravelTimeInSeconds": 1500,
                            "trafficDelayInSeconds": 300,
                        },
                        "legs": [
                            {
                                "points": [
                                    {"latitude": 51.0, "longitude": -0.1},
                                    {"latitude": 51.1, "longitude": -0.15},
                                    {"latitude": 51.5, "longitude": -0.2},
                                ]
                            }
                        ],
                    }
                ]
            }
        )


def test_tomtom_client_returns_google_like_shape() -> None:
    client = TomTomClient("secret", session=FakeSession(), timezone="UTC")

    result = client.directions(
        "Origin Address",
        "Destination Address",
        departure_time=datetime(2024, 10, 10, 7, 0),
    )

    assert len(result) == 1
    route = result[0]
    leg = route["legs"][0]
    assert leg["start_address"] == "Origin Address"
    assert leg["end_address"] == "Destination Address"
    assert leg["duration"]["value"] == pytest.approx(1500.0)
    assert leg["duration_in_traffic"]["value"] == pytest.approx(1800.0)
    assert route["overview_polyline"]["points"]
