# AutoFly

AutoFly is an open-source, self-hosted service that watches configured flight fares, keeps price history, and notifies you when your rules match.

> **Alpha software:** v0.1 uses an unofficial Google Flights integration provided by Flight GOAT. It can break when upstream behavior changes. Always verify dates, fare, baggage, transfers, and availability before booking.

## Features

- Multiple independent YAML watches and arbitrary supported IATA airport identifiers
- Airport-to-airport route expansion with strict validation and a per-cycle safety budget
- Exact one-way and exact round-trip searches
- Rolling and fixed-range one-way cheapest-date discovery followed by exact verification
- Replaceable fare-source and notification interfaces
- SQLite price history, stable itinerary identity, cooldowns, and price-drop/reappearance alerts
- Telegram and versioned JSON webhook notifications
- One-shot CLI suitable for systemd timers, cron, and Docker Compose
- No AutoFly account, telemetry, public port, paid fare API, AI model, or GPU

AutoFly discovers fares and provides a search or booking link. It never purchases tickets.

## Quick start

### Docker Compose (recommended)

Docker builds the pinned Flight GOAT version inside the image. No Python, Node, Go, GPU,
or inbound port is required on the host.

```bash
git clone https://github.com/Breadstick-Crumbs/AutoFly.git
cd AutoFly
docker compose run --rm setup
docker compose run --rm autofly doctor
docker compose run --rm autofly check --all --dry-run
docker compose up -d autofly
```

The setup wizard creates private `config.yaml` and `.env` files without overwriting existing
ones. The `autofly` container runs a cycle immediately, then every configured interval plus
randomized jitter. `docker compose logs -f autofly` shows its status.

On Linux, if your account is not UID/GID 1000, run setup with your IDs so the generated files
belong to you:

```bash
AUTOFLY_UID=$(id -u) AUTOFLY_GID=$(id -g) docker compose run --rm setup
```

### Native Linux

Python 3.12 is recommended (3.11 is supported). The installer uses no `sudo`: it creates
`.venv`, installs the verified Go toolchain under `~/.local/share/autofly`, and builds the exact
tested Flight GOAT commit into `~/.local/bin`.

```bash
git clone https://github.com/Breadstick-Crumbs/AutoFly.git
cd AutoFly
./scripts/install.sh
export PATH="$HOME/.local/bin:$PATH"
./.venv/bin/autofly setup
set -a; . ./.env; set +a
./.venv/bin/autofly doctor --config ./config.yaml
./.venv/bin/autofly check --all --dry-run --config ./config.yaml
```

Use the supplied systemd timer for unattended native operation. See
[`docs/deployment.md`](docs/deployment.md). `autofly init` remains available for users who prefer
to edit the full starter YAML manually, and never overwrites an existing file.

```yaml
version: 1
database: {path: ./data/autofly.db}
scheduler: {interval_hours: 6, jitter_minutes: 10, timezone: Asia/Kolkata}
watches:
  - id: sample-cok-dxb
    origins: [COK]
    destinations: [DXB]
    trip: {type: one_way, adults: 1, cabin: economy}
    dates: {mode: rolling, days_from_now: 1, days_to: 30}
    deal: {currency: INR, maximum_price: 25000, max_stops: 1}
```

See [`config.example.yaml`](config.example.yaml) for two complete watches and [`docs/configuration.md`](docs/configuration.md) for every setting.

## Notifications

For Telegram, create a bot with BotFather, message it once, determine the target chat ID, then set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in the service environment. Enable `notifications.telegram` in YAML and run `autofly notify-test`. Secrets are read only from the named environment variables and are never written to the database or logs.

The generic webhook emits the versioned schema documented in [`docs/notifications.md`](docs/notifications.md). HTTPS is required; loopback/private destinations require an explicit opt-in.

## Deployment

- Docker: run the setup wizard and `docker compose up -d autofly`; scheduling is built in.
- Native/systemd/cron: see [`docs/deployment.md`](docs/deployment.md).
- Troubleshooting: see [`docs/troubleshooting.md`](docs/troubleshooting.md).

No HTTP port is exposed. ARM64 and AMD64 are supported when Python, Node, and Flight GOAT are available for the architecture; NVIDIA GPUs are unused.

## Extending AutoFly

Fare sources implement `FareSource.search(SearchRequest) -> list[FareOffer]`; read [`docs/adding-fare-source.md`](docs/adding-fare-source.md). Notification providers implement the notifier protocol described in [`docs/adding-notifier.md`](docs/adding-notifier.md). Architecture and data flow are in [`docs/architecture.md`](docs/architecture.md).

## Identifier support and limitations

Flight GOAT accepts three-character identifiers passed to Google Flights. Airport codes including `COK`, `DXB`, `LHR`, and `JFK` were verified through upstream fixtures or the v0.1 live check. Metropolitan codes such as `LON` and `NYC` are syntactically accepted by AutoFly but are **not claimed as verified** by this release; test them with `autofly check --watch ID --dry-run`, then a low-volume live check. Descriptive city names are not accepted.

Flexible round trips are deliberately rejected because pairing departure and return dates can create an uncontrolled Cartesian search. v0.1 supports exact round-trip fare searches, verified live against Flight GOAT; its results identify the outbound selection and total search price while the exact return date remains search-level data, not a fully selected inbound itinerary. There is no currency conversion: source and watch currencies must match. Baggage is reported as “verify before booking” unless explicitly supplied by a future source. The Playwright adapter is opt-in and stops at CAPTCHA/bot verification; it does not evade controls.

## Privacy and security

All configuration, secrets, browser state, and fare history stay on your host. AutoFly has no telemetry or central service. Use a dedicated non-root account, protect the environment file, keep browser profiles private, and review webhook destinations. See [`SECURITY.md`](SECURITY.md).

## Project status, license, and attribution

This is the initial `0.1.0` alpha. AutoFly uses Apache-2.0 because its explicit patent grant and permissive contribution terms are suitable for an adapter ecosystem; see [`LICENSE`](LICENSE). Contributions are welcome under [`CONTRIBUTING.md`](CONTRIBUTING.md).

The primary adapter invokes [Flight GOAT](https://printingpress.dev/library/travel/flight-goat), created by Matt Van Horn and contributors as part of the [Printing Press Library](https://github.com/mvanhorn/printing-press-library). The tested release is `2026.8.1`, source commit `854c0465aaa9c275485338c2be7ef0bcaddc4e89`; its directory contains an Apache-2.0 license and NOTICE. The npm installer wrapper `@mvanhorn/printing-press-library@0.1.19` reports an MIT license. AutoFly does not copy its source; the optional Docker build compiles the pinned external CLI and retains the required license/NOTICE attribution in the image.

AutoFly is not affiliated with or endorsed by Google, Google Flights, any airline, Flight GOAT, Printing Press, or their contributors. Fare data may be delayed, incomplete, or wrong. Use search links at your own discretion and comply with applicable terms and laws.
