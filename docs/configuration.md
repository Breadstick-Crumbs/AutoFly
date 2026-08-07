# Configuration

For a first installation, `autofly setup` interactively creates a validated `config.yaml` and
private `.env` file. Add `--docker` when the files will be used by Docker Compose. The command
checks both target paths before prompting and never overwrites either file. Advanced users can copy
`config.example.yaml` or run `autofly init` and edit every setting directly.

Run `autofly init`, then validate with `autofly config validate`. YAML `version` must be `1`. Errors include the broken field and watch ID where available.

## Top-level settings

- `database.path`: persistent SQLite file. `busy_timeout_seconds` controls lock waiting.
- `scheduler.interval_hours`: documentation/scheduler cadence; one-shot checks do not sleep. `jitter_minutes` is applied by systemd's randomized delay or the external scheduler. `timezone` is an IANA name. `max_queries_per_cycle` is a hard safety limit. `lock_path` prevents overlap.
- `sources.flight_goat`: enable flag, executable name/path, minimum pace (never below two seconds), subprocess timeout, output limit, tested version, retries, and maximum exact verifications after each date scan.
- `sources.playwright`: disabled by default; persistent profile, locale, currency, headless mode, diagnostics, and timeout. It is selected only when Flight GOAT is disabled explicitly. v0.1 browser fallback supports exact dates only.
- `notifications`: providers are independently enabled and name environment variables rather than containing secrets.
- `notifications.telegram.control_enabled`: opt into the private long-polling Telegram management
  interface. The configured chat ID must be a private chat. `poll_timeout_seconds` controls only
  the Bot API long-poll duration, not fare-search timing.

## Watches

IDs are lowercase-safe identifiers up to 64 characters; this prevents path traversal. Origins and destinations are unique three-letter ASCII identifiers. Their Cartesian route pairs must not contain origin=destination. Multiple watches are independent, including when they intentionally monitor the same route.

`trip.type` is `one_way` or `round_trip`; adults are 1–9 and cabin is `economy`, `premium_economy`, `business`, or `first`.

Date modes:

- `exact`: `departure`; exact round trips additionally require `return` after departure. Flight GOAT returns a round-trip search price and outbound selection; the return date is retained, but v0.1 does not invent a selected inbound flight/time.
- `range`: inclusive `departure_start` and `departure_end`, one-way only in v0.1.
- `rolling`: inclusive `days_from_now` through `days_to`, recalculated each cycle, one-way only.

Flexible round trips are rejected rather than producing a Cartesian explosion.

Deals require source currency to exactly match `deal.currency`; AutoFly does no conversion. Price must be strictly below `maximum_price`. Unknown stops/layovers/self-transfer fail a restriction that needs that field. A direct flight is known not to self-transfer; connecting Flight GOAT results currently have unknown self-transfer status. A usable HTTP(S) search link is mandatory.

`notifications.cooldown_hours` controls reappearance alerts. A lower price alerts when either configured amount or percentage is met. Successful identical alerts are not repeated.

## Environment overrides

- `AUTOFLY_CONFIG`
- `AUTOFLY_DATABASE_PATH`
- `AUTOFLY_LOCK_PATH`
- `AUTOFLY_MAX_QUERIES_PER_CYCLE`
- `AUTOFLY_FLIGHT_GOAT_COMMAND`
- Provider secrets named in YAML; defaults are `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, and `AUTOFLY_WEBHOOK_URL`.

The root [`config.example.yaml`](../config.example.yaml) includes two watches. Airport identifiers `COK`, `DXB`, `LHR`, and `JFK` are verified by live output or upstream fixtures. Metropolitan identifiers such as `LON` and `NYC` are accepted syntactically but unverified in this release; no claim is made that Flight GOAT resolves them reliably.
