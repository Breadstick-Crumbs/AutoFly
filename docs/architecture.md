# Architecture

AutoFly is a one-shot pipeline; it does not need a web server.

```text
YAML + environment
       |
       v
validated watches -> route/date strategy -> query budget + process lock
                                             |
                                             v
                                  FareSource protocol
                                 /                   \
                          Flight GOAT          Playwright (opt-in)
                                 \                   /
                                  normalized FareOffer
                                             |
                         SQLite observation + deal rules
                                             |
                              alert decision per provider
                                  /                  \
                             Telegram            JSON webhook
```

`SearchRequest` and `FareOffer` isolate scheduler, filtering, storage, and notifications from source-specific schemas. `FareSource` exposes exact search and cheapest-date discovery. Flexible one-way watches use discovery once per route, retain dates below 110% of the threshold, cap candidates, then verify each exact date. Exact watches skip discovery. Every source call consumes the hard per-cycle budget.

SQLite uses schema versioning, foreign keys, a busy timeout, WAL mode, and `BEGIN IMMEDIATE` transactions. It stores cycle/request audit data, immutable observations, current itinerary availability, notification attempts, failures, rate limits, and health state. Itinerary identity excludes changing price. Delivery state is provider-specific, so a failed webhook can retry without duplicating a successful Telegram alert.

The OS advisory lock prevents overlapping cycles. systemd/cron/Docker invoke the same one-shot command. Structured cycle logs contain IDs and counts but never secrets, cookies, headers, or profiles.

## Extension boundaries

- Fare sources implement `autofly.sources.base.FareSource`.
- Notifiers implement `autofly.notifications.base.NotificationProvider`.
- Date modes implement the `DateStrategy` protocol.
- Database changes increment `SCHEMA_VERSION` and add forward-only migrations.

