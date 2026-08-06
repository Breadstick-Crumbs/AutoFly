from __future__ import annotations

import ipaddress
import os
import socket
import time
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx

from autofly.config import WebhookConfig
from autofly.notifications.base import Notification
from autofly.notifications.telegram import NotificationError


class WebhookNotifier:
    name = "webhook"

    def __init__(
        self,
        config: WebhookConfig,
        *,
        client: httpx.Client | None = None,
        resolver: Callable[..., list[tuple]] = socket.getaddrinfo,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.config = config
        self.url = os.environ.get(config.url_env)
        if not self.url:
            raise NotificationError(f"Webhook requires {config.url_env}")
        validate_webhook_url(self.url, config.allow_private_networks, resolver)
        self.client = client or httpx.Client(timeout=config.timeout_seconds, follow_redirects=False)
        self._sleep = sleeper

    def send(self, notification: Notification, idempotency_key: str) -> None:
        payload = webhook_payload(notification, idempotency_key)
        for attempt in range(3):
            try:
                response = self.client.post(
                    self.url,
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Idempotency-Key": idempotency_key,
                        "User-Agent": "AutoFly/0.1",
                    },
                )
                if (response.status_code == 429 or response.status_code >= 500) and attempt < 2:
                    self._sleep(2**attempt)
                    continue
                response.raise_for_status()
                return
            except httpx.HTTPError as exc:
                if attempt < 2:
                    self._sleep(2**attempt)
                    continue
                # Do not echo a potentially secret-bearing webhook URL.
                raise NotificationError("Webhook delivery failed after 3 attempts") from exc
        raise AssertionError("unreachable")


def webhook_payload(notification: Notification, idempotency_key: str) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "event": notification.event,
        "event_id": idempotency_key,
        "occurred_at": datetime.now(UTC).isoformat(),
        "watch_id": notification.watch_id,
        "reason": notification.reason,
        "previous_price": str(notification.previous_price)
        if notification.previous_price is not None
        else None,
        "offer": notification.offer.safe_dict() if notification.offer else None,
    }


def validate_webhook_url(
    url: str, allow_private: bool, resolver: Callable[..., list[tuple]] = socket.getaddrinfo
) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise NotificationError("Webhook URL must be HTTPS with no embedded credentials")
    if allow_private:
        return
    try:
        addresses = resolver(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise NotificationError("Webhook hostname could not be resolved") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise NotificationError(
                "Webhook resolves to a private, loopback, link-local, or reserved address; "
                "set allow_private_networks only for a trusted local endpoint"
            )
