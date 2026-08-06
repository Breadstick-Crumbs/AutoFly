# Troubleshooting

## Setup refuses to overwrite files

This is intentional. Back up or rename the existing `autofly-config/config.yaml` or `.env`, then
run setup again.
AutoFly never replaces a watch configuration or secret file automatically.

## Native Flight GOAT installation fails

`scripts/install-flight-goat.sh` requires outbound HTTPS plus `curl`, `tar`, and `sha256sum`. It
supports Linux AMD64 and ARM64. A checksum failure stops installation; do not bypass it. Remove only
the incomplete version directory reported by the script and retry, or use the Docker path.

Ensure `~/.local/bin` is available to the process running AutoFly:

```bash
export PATH="$HOME/.local/bin:$PATH"
flight-goat-pp-cli --version
```

## Compose setup cannot write config.yaml

On Linux, pass your numeric account IDs:

```bash
AUTOFLY_UID=$(id -u) AUTOFLY_GID=$(id -g) docker compose run --rm setup
```

## Dashboard rejects a remote bind

Non-loopback dashboard listeners require both `--allow-remote` and an
`AUTOFLY_WEB_PASSWORD` of at least 16 characters. Prefer the default loopback listener with an SSH
tunnel. See [`dashboard.md`](dashboard.md).

## Dashboard edits fail with a permission error

The dashboard must be able to create a temporary file next to `config.yaml` for atomic updates.
With Compose, use `autofly-config/config.yaml` and ensure the directory belongs to the UID/GID
selected by `AUTOFLY_UID` and `AUTOFLY_GID`. Do not mount a single config file over the directory.

## Configuration fails

Run `autofly config validate --json`. Errors identify the field and, where possible, watch ID. Common causes are duplicate identifiers, origin=destination, invalid IANA timezones, flexible round trips, and route counts above the explicit safety limit.

## Flight GOAT is missing or has a version warning

Run `flight-goat-pp-cli --version`, `flight-goat-pp-cli flights --help`, `flight-goat-pp-cli dates --help`, and `flight-goat-pp-cli doctor`. AutoFly v0.1 was tested against `2026.8.1` at commit `854c0465aaa9c275485338c2be7ef0bcaddc4e89`. Reinstall with the pinned npm wrapper documented in deployment, or set `AUTOFLY_FLIGHT_GOAT_COMMAND` to the absolute executable path.

Flight GOAT doctor may mention missing FlightAware credentials; Google Flights fare commands do not require those credentials. AutoFly never uses FlightAware AeroAPI as a fare source.

## No alert arrived

A successful search with no deal is normal. Check `autofly history --watch ID`, the strict-below threshold, currency equality, stops, layover, self-transfer, and search link. Flight GOAT does not currently confirm self-transfer for connecting results, so `allow_self_transfer: false` rejects unknown connecting results. Direct results are known false. Check provider environment variables and run `autofly notify-test`.

Identical successful alerts are intentionally deduplicated. A new alert needs first qualification, a configured price drop, or reappearance after cooldown.

## Rate limited

Exit code 7/HTTP 429 stops the current cycle. Do not retry rapidly, parallelize searches, rotate proxies, or lower pacing below the supported minimum. Wait, reduce watches/date candidates, increase `pace_seconds`, and let the next scheduled cycle run. `soar`/other backends are not automatically substituted.

## Lock unavailable or SQLite busy

Another cycle is probably running. Inspect processes and scheduler logs; do not delete a live lock. Advisory locks release when the owning process exits. SQLite uses WAL and a busy timeout. Put state on a local persistent filesystem, not an unreliable network share, and never run multiple containers against one database.

## Playwright failure or CAPTCHA

Playwright is disabled by default and selected only after explicitly disabling Flight GOAT and enabling Playwright. Install `autofly[playwright]` and `playwright install chromium`. AutoFly stops at bot verification and will not solve or bypass it. Diagnostics contain no URL, cookies, headers, HTML, or profile copy; screenshots hide the page header/account UI where possible and remain local. Flexible date discovery is unsupported.
