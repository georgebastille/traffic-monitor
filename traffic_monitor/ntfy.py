from __future__ import annotations

import base64

import requests

from traffic_monitor.notifications import LEVEL_PRIORITY, Notification

LEVEL_TAGS = {"green": "green_circle", "orange": "orange_circle", "red": "red_circle"}


def send_ntfy(topic: str, notification: Notification, *, session: requests.Session | None = None) -> None:
    """Publish to ntfy.sh; the level tag becomes an emoji prepended to the title."""
    (session or requests).post(
        f"https://ntfy.sh/{topic}",
        data=notification.body.encode("utf-8"),
        headers={
            "Title": _header_value(notification.title),
            "Tags": LEVEL_TAGS[notification.level],
            "Priority": str(LEVEL_PRIORITY[notification.level]),
        },
        timeout=30,
    )


def _header_value(text: str) -> str:
    """requests sends headers as Latin-1, so wrap non-ASCII text in RFC 2047 encoding, which ntfy decodes."""
    if text.isascii():
        return text
    return f"=?UTF-8?B?{base64.b64encode(text.encode('utf-8')).decode('ascii')}?="
