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


def test_tomtom_client_returns_route_result() -> None:
    session = FakeSession()
    client = TomTomClient("secret", session=session, timezone="UTC")

    result = client.route(
        "Origin Address",
        "Destination Address",
        waypoints=[(51.2, -0.12)],
        depart_at=datetime(2024, 10, 10, 7, 0),
    )

    assert result.traffic_secs == pytest.approx(1800.0)
    assert result.clear_secs == pytest.approx(1500.0)
    assert result.points == [(51.0, -0.1), (51.1, -0.15), (51.5, -0.2)]
    route_url, params = session._calls[-1]
    assert "51.000000,-0.100000:51.200000,-0.120000:51.500000,-0.200000" in route_url
    assert params["departAt"] == "2024-10-10T07:00:00"


def test_tomtom_client_persists_geocodes(tmp_path) -> None:
    cache_path = tmp_path / "geocode_cache.json"
    first_session = FakeSession()
    TomTomClient("secret", session=first_session, geocode_cache_path=cache_path).route("Origin", "Destination")
    assert sum("search" in url for url, _ in first_session._calls) == 2

    second_session = FakeSession()
    TomTomClient("secret", session=second_session, geocode_cache_path=cache_path).route("Origin", "Destination")
    assert [url for url, _ in second_session._calls if "search" in url] == []
