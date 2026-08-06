import json
import socket
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from autofly.config import TelegramConfig, WebhookConfig
from autofly.models import FareOffer
from autofly.notifications.base import Notification
from autofly.notifications.telegram import (
    MARKDOWN_V2_SPECIAL,
    NotificationError,
    TelegramNotifier,
    escape_markdown,
    format_telegram,
)
from autofly.notifications.webhook import (
    WebhookNotifier,
    validate_webhook_url,
    webhook_payload,
)


def deal_notification() -> Notification:
    offer = FareOffer(
        source="flight_goat",
        origin="COK",
        destination="DXB",
        departure_at=datetime(2026, 8, 20, tzinfo=UTC),
        airline="Air India Express",
        flight_numbers=["IX425"],
        stops=0,
        layover_minutes=[],
        duration_minutes=250,
        trip_type="one_way",
        cabin="economy",
        passenger_count=1,
        price=Decimal("18740"),
        currency="INR",
        booking_url="https://example.com/search?a=1",
        self_transfer=False,
    )
    return Notification(
        event="deal",
        watch_id="kerala-to-uae",
        reason="price_drop",
        offer=offer,
        previous_price=Decimal("21300"),
    )


def test_telegram_escape_covers_all_special_characters() -> None:
    escaped = escape_markdown(MARKDOWN_V2_SPECIAL)
    for char in MARKDOWN_V2_SPECIAL:
        assert f"\\{char}" in escaped


def test_telegram_message_is_safe_and_honest_about_baggage() -> None:
    message = format_telegram(deal_notification())
    assert "AutoFly deal found" in message
    assert "Baggage: verify before booking" in message
    assert "₹18,740" in message
    assert "₹21,300" in message


def test_telegram_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    TelegramNotifier(TelegramConfig(enabled=True), client=client).send(deal_notification(), "id")
    body = json.loads(seen[0].content)
    assert body["chat_id"] == "123"
    assert body["parse_mode"] == "MarkdownV2"


def test_missing_telegram_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    with pytest.raises(NotificationError, match="requires"):
        TelegramNotifier(TelegramConfig(enabled=True))


def public_resolver(*args: object, **kwargs: object) -> list[tuple]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


def private_resolver(*args: object, **kwargs: object) -> list[tuple]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]


def test_webhook_url_ssrf_protection() -> None:
    validate_webhook_url("https://example.com/hook", False, public_resolver)
    with pytest.raises(NotificationError, match="private"):
        validate_webhook_url("https://localhost/hook", False, private_resolver)
    with pytest.raises(NotificationError, match="HTTPS"):
        validate_webhook_url("http://example.com/hook", False, public_resolver)
    with pytest.raises(NotificationError, match="credentials"):
        validate_webhook_url("https://user:pass@example.com/hook", False, public_resolver)


def test_versioned_webhook_payload() -> None:
    payload = webhook_payload(deal_notification(), "event-123")
    assert payload["schema_version"] == "1.0"
    assert payload["event_id"] == "event-123"
    assert payload["event"] == "deal"
    assert payload["offer"]["currency"] == "INR"  # type: ignore[index]


def test_webhook_delivery_and_idempotency_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOFLY_WEBHOOK_URL", "https://example.com/hook")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    notifier = WebhookNotifier(WebhookConfig(enabled=True), client=client, resolver=public_resolver)
    notifier.send(deal_notification(), "event-123")
    assert seen[0].headers["Idempotency-Key"] == "event-123"


def test_webhook_retries_transient_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOFLY_WEBHOOK_URL", "https://example.com/hook")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503 if calls < 3 else 204)

    notifier = WebhookNotifier(
        WebhookConfig(enabled=True),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=public_resolver,
        sleeper=lambda _: None,
    )
    notifier.send(deal_notification(), "event-123")
    assert calls == 3
