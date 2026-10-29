from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any


@dataclass
class RouteConfig:
    id: str
    name: str
    origin: str
    destination: str
    arrival_time: str  # "HH:MM" 24-hour
    timezone: str  # e.g. "Europe/London"
    active: bool = False


@dataclass
class AppConfig:
    routes: list[RouteConfig]

    @property
    def active_route(self) -> RouteConfig | None:
        for route in self.routes:
            if route.active:
                return route
        return None

    @classmethod
    def load(cls, path: Path | str) -> "AppConfig":
        source = Path(path)
        if not source.exists():
            return cls(routes=[])
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return cls(routes=[])
        known = {field.name for field in fields(RouteConfig)}
        routes = [RouteConfig(**{k: v for k, v in r.items() if k in known}) for r in payload.get("routes", [])]
        return cls(routes=routes)

    def save(self, path: Path | str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {"routes": [asdict(r) for r in self.routes]}
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def set_active(self, route_id: str) -> None:
        found = False
        for route in self.routes:
            if route.id == route_id:
                route.active = True
                found = True
            else:
                route.active = False
        if not found:
            raise ValueError(f"Route {route_id!r} not found")
