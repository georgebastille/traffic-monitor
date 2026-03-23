from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from traffic_monitor.analytics import load_samples
from traffic_monitor.config import AppConfig, RouteConfig
from traffic_monitor.dependencies import require_token

app = FastAPI(title="Traffic Monitor")

Auth = Annotated[None, Depends(require_token)]


def _data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", "."))


def _config_path() -> Path:
    return _data_dir() / "routes.json"


def _push_sub_path() -> Path:
    return _data_dir() / "push_subscription.json"


def _load_config() -> AppConfig:
    return AppConfig.load(_config_path())


def _save_config(config: AppConfig) -> None:
    config.save(_config_path())


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@app.get("/api/status")
def get_status(_: Auth) -> JSONResponse:
    config = _load_config()
    route = config.active_route
    if route is None:
        raise HTTPException(status_code=404, detail="No active route")
    jsonl = _data_dir() / f"{route.id}_traffic.jsonl"
    samples = load_samples(jsonl)
    if not samples:
        raise HTTPException(status_code=404, detail="No data yet")
    latest = samples[-1]
    return JSONResponse({
        "route_id": route.id,
        "route_name": route.name,
        "query_time": latest.query_time.isoformat(),
        "traffic_duration_mins": latest.traffic_duration_mins,
        "clear_duration_mins": latest.clear_duration_mins,
        "delay_mins": round(latest.traffic_duration_mins - latest.clear_duration_mins, 1),
    })


# ---------------------------------------------------------------------------
# Routes CRUD
# ---------------------------------------------------------------------------

class RouteIn(BaseModel):
    id: str
    name: str
    origin: str
    destination: str
    arrival_time: str
    timezone: str = "Europe/London"
    provider: str = "tomtom"


@app.get("/api/routes")
def list_routes(_: Auth) -> JSONResponse:
    config = _load_config()
    return JSONResponse({"routes": [asdict(r) for r in config.routes]})


@app.post("/api/routes", status_code=201)
def create_route(body: RouteIn, _: Auth) -> JSONResponse:
    config = _load_config()
    if any(r.id == body.id for r in config.routes):
        raise HTTPException(status_code=409, detail=f"Route {body.id!r} already exists")
    new_route = RouteConfig(
        id=body.id,
        name=body.name,
        origin=body.origin,
        destination=body.destination,
        arrival_time=body.arrival_time,
        timezone=body.timezone,
        provider=body.provider,
        active=len(config.routes) == 0,  # auto-activate if first route
    )
    config.routes.append(new_route)
    _save_config(config)
    return JSONResponse(asdict(new_route))


@app.put("/api/routes/{route_id}")
def update_route(route_id: str, body: RouteIn, _: Auth) -> JSONResponse:
    config = _load_config()
    for i, route in enumerate(config.routes):
        if route.id == route_id:
            config.routes[i] = RouteConfig(
                id=route_id,
                name=body.name,
                origin=body.origin,
                destination=body.destination,
                arrival_time=body.arrival_time,
                timezone=body.timezone,
                provider=body.provider,
                active=route.active,
            )
            _save_config(config)
            return JSONResponse(asdict(config.routes[i]))
    raise HTTPException(status_code=404, detail=f"Route {route_id!r} not found")


@app.delete("/api/routes/{route_id}", status_code=204)
def delete_route(route_id: str, _: Auth) -> None:
    config = _load_config()
    before = len(config.routes)
    config.routes = [r for r in config.routes if r.id != route_id]
    if len(config.routes) == before:
        raise HTTPException(status_code=404, detail=f"Route {route_id!r} not found")
    _save_config(config)


@app.post("/api/routes/{route_id}/activate")
def activate_route(route_id: str, _: Auth) -> JSONResponse:
    config = _load_config()
    try:
        config.set_active(route_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Route {route_id!r} not found")
    _save_config(config)
    return JSONResponse({"active": route_id})


# ---------------------------------------------------------------------------
# Data / chart
# ---------------------------------------------------------------------------

@app.get("/api/chart/{route_id}")
def get_chart(route_id: str, _: Auth) -> FileResponse:
    png = _data_dir() / f"{route_id}_anomaly.png"
    if not png.exists():
        raise HTTPException(status_code=404, detail="Chart not available yet")
    return FileResponse(png, media_type="image/png")


@app.get("/api/history/{route_id}")
def get_history(route_id: str, _: Auth, n: int = 200) -> JSONResponse:
    jsonl = _data_dir() / f"{route_id}_traffic.jsonl"
    samples = load_samples(jsonl)
    tail = samples[-n:] if len(samples) > n else samples
    records = [
        {
            "query_time": s.query_time.isoformat(),
            "traffic_duration_mins": s.traffic_duration_mins,
            "clear_duration_mins": s.clear_duration_mins,
        }
        for s in tail
    ]
    return JSONResponse({"route_id": route_id, "samples": records})


# ---------------------------------------------------------------------------
# Push notifications
# ---------------------------------------------------------------------------

class PushSubscriptionIn(BaseModel):
    endpoint: str
    keys: dict[str, str]


@app.get("/api/push/public-key")
def get_push_public_key(_: Auth) -> JSONResponse:
    key = os.getenv("VAPID_PUBLIC_KEY", "")
    if not key:
        raise HTTPException(status_code=503, detail="VAPID not configured")
    return JSONResponse({"public_key": key})


@app.post("/api/push/subscribe", status_code=201)
def subscribe_push(body: PushSubscriptionIn, _: Auth) -> JSONResponse:
    sub_path = _push_sub_path()
    sub_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"endpoint": body.endpoint, "keys": body.keys}
    sub_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return JSONResponse({"status": "subscribed"})


@app.delete("/api/push/subscribe", status_code=204)
def unsubscribe_push(_: Auth) -> None:
    sub_path = _push_sub_path()
    if sub_path.exists():
        sub_path.unlink()


# ---------------------------------------------------------------------------
# Static PWA — mounted last so API routes take precedence
# ---------------------------------------------------------------------------

_static_dir = Path(__file__).parent.parent / "static"
if _static_dir.exists():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")
