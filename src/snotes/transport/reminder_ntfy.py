"""Reminder R1 ntfy transport adapter."""

from __future__ import annotations

from email.header import Header
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from src.snotes.application.ports import ReminderNotificationTransport


class _RejectRedirects(HTTPRedirectHandler):
    """Keep an authenticated ntfy request on its configured origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class NtfyReminderTransport(ReminderNotificationTransport):
    def __init__(
        self,
        endpoint: str,
        *,
        timeout_seconds: float = 10.0,
        bearer_token: str | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/") + "/"
        self.timeout_seconds = float(timeout_seconds)
        self.bearer_token = bearer_token.strip() if bearer_token is not None else None
        if self.bearer_token is not None:
            parsed = urlsplit(self.endpoint)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.hostname.casefold() == "ntfy.sh"
                or parsed.username
                or parsed.password
            ):
                raise ValueError("authenticated_reminder_ntfy_https_endpoint_required")
            if not self.bearer_token or any(character.isspace() for character in self.bearer_token):
                raise ValueError("reminder_ntfy_bearer_token_invalid")

    def send(self, *, topic: str, title: str, message: str) -> None:
        if not self.endpoint.strip("/"):
            raise ValueError("reminder_ntfy_endpoint_required")
        clean_topic = topic.strip()
        if not clean_topic:
            raise ValueError("reminder_ntfy_topic_required")
        headers = {
            # ntfy accepts RFC 2047; urllib cannot encode Chinese headers.
            "Title": Header(title, "utf-8").encode() if not title.isascii() else title,
            "Tags": "bell",
        }
        if self.bearer_token is not None:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        request = Request(
            urljoin(self.endpoint, quote(clean_topic, safe="")),
            data=message.encode("utf-8"),
            headers=headers,
            method="POST",
        )
        # urllib preserves Authorization on redirects. Disable redirects for the
        # authenticated Cloud adapter so a credential can never cross origins.
        open_request = (
            build_opener(_RejectRedirects()).open
            if self.bearer_token is not None
            else urlopen
        )
        with open_request(request, timeout=self.timeout_seconds) as response:
            if int(response.status) >= 400:
                raise RuntimeError(f"ntfy_send_failed:{response.status}")
