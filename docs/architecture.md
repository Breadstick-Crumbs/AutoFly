# Architecture

AutoFly's monitoring core is a one-shot pipeline and does not depend on a web server. The optional
dashboard is a separate administration surface that calls the same validated configuration,
database, engine, query-budget, and process-lock layers.

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

Schema version 2 stores the qualification boolean and reason beside each observation. This keeps
dashboard filtering and explanations faithful to the exact watch snapshot evaluated during the
search. Older rows remain nullable and are never retroactively classified.

The OS advisory lock prevents overlapping cycles. systemd/cron/Docker invoke the same one-shot command. Structured cycle logs contain IDs and counts but never secrets, cookies, headers, or profiles.

The optional FastAPI dashboard reads sanitized summaries from SQLite. Watch mutations pass through
full Pydantic validation and atomically replace YAML while retaining one backup. Manual checks run
as a single in-process background job and still acquire the OS lock. Static browser assets are
bundled locally; no CDN, analytics, telemetry, or browser-side secret access is used.

## Extension boundaries

- Fare sources implement `autofly.sources.base.FareSource`.
- Notifiers implement `autofly.notifications.base.NotificationProvider`.
- Date modes implement the `DateStrategy` protocol.
- Database changes increment `SCHEMA_VERSION` and add forward-only migrations.
