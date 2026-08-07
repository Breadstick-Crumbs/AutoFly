# Notifications

Telegram messages use safely escaped MarkdownV2 and always say `Baggage: verify before booking` unless the source explicitly supplies baggage data. Create a bot through BotFather, message it once, set the token/chat environment variables, enable the provider, and run `autofly notify-test`.

The same bot can optionally run a private long-polling control interface for managing watches and
starting searches. It is disabled by default and restricted to the configured private chat. See
[`telegram-control.md`](telegram-control.md).

Webhook requests are HTTPS POSTs with `Content-Type: application/json`, `User-Agent: AutoFly/0.1`, and an `Idempotency-Key`. Schema `1.0` is:

```json
{
  "schema_version": "1.0",
  "event": "deal",
  "event_id": "stable-idempotency-hash",
  "occurred_at": "2026-08-06T12:00:00+00:00",
  "watch_id": "sample",
  "reason": "first_qualified",
  "previous_price": null,
  "offer": {"source": "flight_goat", "price": "18740", "currency": "INR"}
}
```

`event` can also be `health_unhealthy` or `health_recovered`. Receivers should deduplicate on `event_id`. AutoFly retries 429/5xx/transport failures and records every failed or successful delivery. Private/loopback/link-local/reserved webhook targets are blocked unless `allow_private_networks: true` is explicitly set for a trusted local service.
