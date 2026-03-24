from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pywebpush import webpush, WebPushException


@dataclass
class PushSubscription:
    endpoint: str
    keys: dict[str, str]

    @classmethod
    def load(cls, path: Path | str) -> "PushSubscription | None":
        source = Path(path)
        if not source.exists():
            return None
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        endpoint = payload.get("endpoint")
        keys = payload.get("keys")
        if not endpoint or not isinstance(keys, dict):
            return None
        return cls(endpoint=endpoint, keys=keys)

    def save(self, path: Path | str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {"endpoint": self.endpoint, "keys": self.keys}
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def send_push_notification(
    subscription: PushSubscription,
    message: str,
    *,
    vapid_private_key: str,
    vapid_claims: dict[str, str],
) -> None:
    webpush(
        subscription_info={"endpoint": subscription.endpoint, "keys": subscription.keys},
        data=json.dumps({"title": "Traffic", "body": message}),
        vapid_private_key=vapid_private_key,
        vapid_claims=vapid_claims,
    )


def make_push_notifier(
    sub_path: Path,
    vapid_private_key: str,
    vapid_claims: dict[str, str],
) -> Callable[[str], None]:
    """Return a notifier callable suitable for passing to TrafficMonitor."""

    def notify(message: str) -> None:
        subscription = PushSubscription.load(sub_path)
        if subscription is None:
            return
        try:
            send_push_notification(
                subscription,
                message,
                vapid_private_key=vapid_private_key,
                vapid_claims=vapid_claims,
            )
        except WebPushException as exc:
            # Log but don't crash the monitor if push fails
            print(f"[PUSH ERROR] {exc}")

    return notify
