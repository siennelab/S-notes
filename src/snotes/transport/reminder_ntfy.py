"""Reminder R1 ntfy transport adapter."""

from __future__ import annotations

from email.header import Header
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen

from src.snotes.application.ports import ReminderNotificationTransport


class NtfyReminderTransport(ReminderNotificationTransport):
    def __init__(self, endpoint: str, *, timeout_seconds: float = 10.0) -> None:
        self.endpoint = endpoint.rstrip("/") + "/"
        self.timeout_seconds = float(timeout_seconds)

    def send(self, *, topic: str, title: str, message: str) -> None:
        if not self.endpoint.strip("/"):
            raise ValueError("reminder_ntfy_endpoint_required")
        clean_topic = topic.strip()
        if not clean_topic:
            raise ValueError("reminder_ntfy_topic_required")
        request = Request(
            urljoin(self.endpoint, quote(clean_topic, safe="")),
            data=message.encode("utf-8"),
            headers={
                # ntfy accepts RFC 2047; urllib cannot encode Chinese headers.
                "Title": Header(title, "utf-8").encode() if not title.isascii() else title,
                "Tags": "bell",
            },
            method="POST",
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            if int(response.status) >= 400:
                raise RuntimeError(f"ntfy_send_failed:{response.status}")
