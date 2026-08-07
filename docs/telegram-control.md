# Telegram control interface

AutoFly can use the same Telegram bot for deal alerts and as a private control interface. The
interface uses Telegram's long-polling Bot API, so it needs no public webhook, HTTP port, or
firewall change.

## Capabilities

- Create a watch through a guided conversation.
- Set multiple origin and destination IATA codes.
- Choose one-way or exact round-trip travel, cabin, adults, and exact, range, or rolling dates.
- Set currency, maximum price, stops, layover, self-transfer, cooldown, and price-drop rules.
- View, edit, pause, resume, and delete watches.
- Start a safe locked search for one watch or every active watch.
- View recent qualifying deals and open their source links.
- Review monitoring health, the schedule, and the last search.

Telegram never books a ticket. Configuration changes use the same validation, safety budget,
atomic replacement, and backup behavior as the web dashboard.

## Security model

The control interface accepts updates only when both the numeric chat ID and Telegram chat type
match the configured private chat. Group and supergroup controls are deliberately rejected because
a shared `TELEGRAM_CHAT_ID` cannot identify which group member issued a command. Unauthorized
updates receive no response.

The bot token and chat ID stay in environment variables. Callback data contains only short action
codes and non-reversible watch identifiers; it never contains credentials, routes, or prices.
Messages are plain text, and Bot API failures are sanitized before logging or replying.

Long polling cannot be used while the same bot has a Telegram webhook configured. AutoFly checks
for that conflict at startup and stops with a clear error instead of deleting the webhook.

## Enable it

In `config.yaml`:

```yaml
notifications:
  telegram:
    enabled: true
    control_enabled: true
    bot_token_env: TELEGRAM_BOT_TOKEN
    chat_id_env: TELEGRAM_CHAT_ID
```

Set the two environment variables, message the bot once, and run:

```bash
autofly telegram --config /path/to/config.yaml
```

The configured chat must be a private conversation with the bot. Use `/menu` to open the button
interface, `/add` to start guided setup, and `/cancel` to abandon an unfinished edit without
changing configuration.

Only one `autofly telegram` process may consume updates for a bot token. A bot-started fare search
uses AutoFly's normal process lock, pacing, query budget, persistence, and notifier deduplication.

## Docker Compose

The Telegram service needs write access to `autofly-config/` because it edits watches. After
enabling `control_enabled`, start it with the scheduler:

```bash
docker compose --profile telegram up -d autofly telegram
docker compose logs -f telegram
```

The service exposes no port. When the web dashboard is also enabled:

```bash
docker compose --profile web --profile telegram up -d autofly web telegram
```

## systemd

For the single-user installation at `~/autofly`, install the included user service:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/autofly-telegram-user.service ~/.config/systemd/user/autofly-telegram.service
systemctl --user daemon-reload
systemctl --user enable --now autofly-telegram.service
journalctl --user -u autofly-telegram.service -f
```

The unit uses `%h`, so no username is embedded in the file. It expects configuration and secrets
under `~/.config/autofly/` and persistent state under `~/.local/share/autofly/`.

For a dedicated system account, `deploy/systemd/autofly-telegram.service` expects its editable
configuration at `/var/lib/autofly/config.yaml`. Copy the reviewed configuration there with owner
`autofly`, update the timer service to read the same path, then enable the control service:

```bash
sudo install -o autofly -g autofly -m 0600 /etc/autofly/config.yaml /var/lib/autofly/config.yaml
sudo systemctl edit autofly.service
# Add: Environment=AUTOFLY_CONFIG=/var/lib/autofly/config.yaml
sudo cp deploy/systemd/autofly-telegram.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now autofly-telegram.service
journalctl -u autofly-telegram.service -f
```

Keep configuration and database backups together. The atomic configuration writer retains one
`config.yaml.bak` generation for quick recovery.

## Current limitations

- One private operator chat per AutoFly instance.
- Text plus inline-button interface; no Telegram Mini App is required.
- Draft conversations are intentionally in memory. Restarting the service cancels an unfinished
  draft without changing configuration.
- Flexible round trips remain unsupported, matching the core fare engine.
- Airport inputs use IATA identifiers accepted by the configured source; the bot does not claim
  that every metropolitan identifier works.
