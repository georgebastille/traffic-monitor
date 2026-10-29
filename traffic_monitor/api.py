from __future__ import annotations

import json
import os
import statistics
from dataclasses import asdict
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from traffic_monitor.analytics import (
    compute_bucket_profile,
    filter_recent_weekday_samples,
    load_samples,
    minutes_since_midnight,
)
from traffic_monitor.config import AppConfig, RouteConfig
from traffic_monitor.dependencies import require_token
from traffic_monitor.monitor import TrafficSample
from traffic_monitor.settings import Settings
from traffic_monitor.status import assess

app = FastAPI(title="Traffic Monitor")


@app.middleware("http")
async def revalidate_everything(request: Request, call_next):
    """Without Cache-Control, browsers heuristically cached app.js for days and kept running the old app."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache"
    return response


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
# Summary: the same verdict the notifications use, plus today's chart data
# ---------------------------------------------------------------------------

DEPARTURE_WINDOW = timedelta(minutes=90)
_samples_cache: dict[Path, tuple[float, list[TrafficSample]]] = {}


def _route_samples(route: RouteConfig) -> list[TrafficSample]:
    """Parse the hot JSONL once per change; the monitor appends every 5 minutes."""
    path = _data_dir() / f"{route.id}_traffic.jsonl"
    if not path.exists():
        return []
    mtime = path.stat().st_mtime
    cached = _samples_cache.get(path)
    if cached is None or cached[0] != mtime:
        cached = (mtime, load_samples(path, tzinfo=ZoneInfo(route.timezone)))
        _samples_cache[path] = cached
    return cached[1]


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


@app.get("/api/summary")
def get_summary(_: Auth) -> JSONResponse:
    route = _load_config().active_route
    if route is None:
        raise HTTPException(status_code=404, detail="No active route")
    samples = _route_samples(route)
    if not samples:
        raise HTTPException(status_code=404, detail="No data yet")
    zone = ZoneInfo(route.timezone)
    now = datetime.now(zone)
    current, history = samples[-1], samples[:-1]
    settings = Settings.from_env()
    result = assess(
        history, current, now=now, arrival_time=time.fromisoformat(route.arrival_time), zone=zone, settings=settings
    )
    departure_active = (
        result.target_arrival.date() == now.date()
        and result.leave_by - DEPARTURE_WINDOW <= now <= result.target_arrival
    )

    today = now.date()
    bucket = 5
    profile = compute_bucket_profile(
        filter_recent_weekday_samples(history, reference=now),
        day=today,
        bucket_minutes=bucket,
        baseline_percentile=settings.baseline_percentile,
    )
    today_values: dict[int, list[float]] = {}
    for sample in samples:
        if sample.query_time.date() == today:
            minute = int(minutes_since_midnight(sample.query_time) // bucket) * bucket
            today_values.setdefault(minute, []).append(sample.traffic_duration_mins)
    minutes = list(range(0, 24 * 60, bucket))

    return JSONResponse(
        {
            "route_id": route.id,
            "route_name": route.name,
            "sample_time": current.query_time.isoformat(),
            "now": {
                "mins": _round(result.current_mins),
                "typical_mins": _round(result.typical_mins),
                "level": result.level,
            },
            "departure": {
                "active": departure_active,
                "leave_by": result.leave_by.isoformat(),
                "arrive_by": result.target_arrival.isoformat(),
                "mins": _round(result.predicted_mins),
                "typical_mins": _round(result.departure_typical_mins),
                "level": result.departure_level,
            },
            "chart": {
                "date": today.isoformat(),
                "bucket_minutes": bucket,
                "minutes": minutes,
                "today": [_round(statistics.fmean(v)) if (v := today_values.get(m)) else None for m in minutes],
                "typical": [_round(profile[m].typical) if m in profile else None for m in minutes],
                "low": [_round(profile[m].low) if m in profile else None for m in minutes],
                "high": [_round(profile[m].high) if m in profile else None for m in minutes],
            },
        }
    )


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
        active=len(config.routes) == 0,  # auto-activate if first route
    )
    config.routes.append(new_route)
    _save_config(config)
    return JSONResponse(asdict(new_route), status_code=201)


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
# History
# ---------------------------------------------------------------------------


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
