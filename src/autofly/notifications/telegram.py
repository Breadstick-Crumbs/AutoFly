from __future__ import annotations

import os
import time
from collections.abc import Callable
from decimal import Decimal

import httpx

from autofly.config import TelegramConfig
from autofly.errors import AutoFlyError
from autofly.notifications.base import Notification

MARKDOWN_V2_SPECIAL = "_[]()~`>#+-=|{}.!\\"


class NotificationError(AutoFlyError):
    pass


class TelegramNotifier:
    name = "telegram"

    def __init__(
        self,
        config: TelegramConfig,
        *,
        client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.config = config
        self.token = os.environ.get(config.bot_token_env)
        self.chat_id = os.environ.get(config.chat_id_env)
        if not self.token or not self.chat_id:
            raise NotificationError(
                f"Telegram requires {config.bot_token_env} and {config.chat_id_env}"
            )
        self.client = client or httpx.Client(timeout=config.timeout_seconds, follow_redirects=False)
        self._sleep = sleeper

    def send(self, notification: Notification, idempotency_key: str) -> None:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        body = {
            "chat_id": self.chat_id,
            "text": format_telegram(notification),
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": True,
        }
        for attempt in range(3):
            try:
                response = self.client.post(url, json=body)
                if (response.status_code == 429 or response.status_code >= 500) and attempt < 2:
                    self._sleep(2**attempt)
                    continue
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or payload.get("ok") is not True:
                    raise NotificationError("Telegram returned an unsuccessful response")
                return
            except (httpx.HTTPError, ValueError) as exc:
                if attempt < 2:
                    self._sleep(2**attempt)
                    continue
                # Exception text can contain the token-bearing request URL, so do not echo it.
                raise NotificationError("Telegram delivery failed after 3 attempts") from exc
        raise AssertionError("unreachable")


def escape_markdown(value: str) -> str:
    return "".join(f"\\{char}" if char in MARKDOWN_V2_SPECIAL else char for char in value)


def format_telegram(notification: Notification) -> str:
    if notification.event != "deal" or notification.offer is None:
        heading = (
            "AutoFly health warning"
            if notification.event == "health_unhealthy"
            else "AutoFly recovered"
        )
        return escape_markdown(f"⚕️ {heading}\n\n{notification.reason}")
    offer = notification.offer
    route = f"{offer.origin} → {offer.destination}"
    date_text = offer.departure_at.strftime("%d %B %Y")
    airline = offer.airline or "Airline unknown"
    stops = "Direct" if offer.stops == 0 else f"{offer.stops} stop(s)"
    price = format_price(offer.price, offer.currency)
    lines = [
        "✈️ AutoFly deal found",
        "",
        route,
        date_text,
        f"{airline} · {stops}",
        price,
        "",
        f"Watch: {notification.watch_id}",
    ]
    if notification.previous_price is not None:
        lines.append(
            f"Previous observed price: {format_price(notification.previous_price, offer.currency)}"
        )
    lines.extend(
        [
            f"Reason: {notification.reason}",
            f"Baggage: {offer.baggage or 'verify before booking'}",
            "",
            f"Open fare: {offer.booking_url}",
        ]
    )
    return escape_markdown("\n".join(lines))


def format_price(price: Decimal, currency: str) -> str:
    symbols = {"INR": "₹", "GBP": "£", "EUR": "€", "USD": "$"}
    symbol = symbols.get(currency, f"{currency} ")
    return f"{symbol}{price:,.2f}".removesuffix(".00")
