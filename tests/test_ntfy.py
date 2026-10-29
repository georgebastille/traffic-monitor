from __future__ import annotations

import base64
from typing import Any

from traffic_monitor.notifications import Notification
from traffic_monitor.ntfy import send_ntfy


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> None:
        self.calls.append({"url": url, **kwargs})


def test_send_ntfy_sets_level_tag_priority_and_encoded_title() -> None:
    session = FakeSession()
    notification = Notification(level="red", title="Leave 07:58 · +6m (22m) · check Waze", body="Check Waze 🚗")

    send_ntfy("topic", notification, session=session)  # type: ignore[arg-type]

    call = session.calls[0]
    assert call["url"] == "https://ntfy.sh/topic"
    assert call["data"] == "Check Waze 🚗".encode()
    assert call["headers"]["Tags"] == "red_circle"
    assert call["headers"]["Priority"] == "4"
    title = call["headers"]["Title"]
    assert title.isascii()
    assert base64.b64decode(title.removeprefix("=?UTF-8?B?").removesuffix("?=")).decode() == notification.title
