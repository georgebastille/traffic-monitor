from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


@dataclass
class NotificationState:
    departure_date: date | None = None
    departure_minutes: float | None = None
    pattern_alert_date: date | None = None

    @classmethod
    def load(cls, path: Path | str) -> "NotificationState":
        source = Path(path)
        if not source.exists():
            return cls()
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return cls()
        return cls(
            departure_date=_parse_date(payload.get("departure_date")),
            departure_minutes=_parse_float(payload.get("departure_minutes")),
            pattern_alert_date=_parse_date(payload.get("pattern_alert_date")),
        )

    def save(self, path: Path | str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {}
        if self.departure_date:
            payload["departure_date"] = self.departure_date.isoformat()
        if self.departure_minutes is not None:
            payload["departure_minutes"] = self.departure_minutes
        if self.pattern_alert_date:
            payload["pattern_alert_date"] = self.pattern_alert_date.isoformat()
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
