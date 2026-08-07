# Security policy

## Supported versions

Until a stable release, only the latest tagged `0.x` release and current default branch receive security fixes.

## Reporting

Do not open a public issue for a vulnerability. Use GitHub's private vulnerability reporting for this repository when available, or contact the repository owner privately. Include affected versions, impact, reproduction steps, and any suggested mitigation. Allow maintainers reasonable time to investigate before disclosure.

Never include real bot tokens, webhook URLs, cookies, browser profiles, or route history in a report. Redact query parameters and authentication headers.

## Security model

- Secrets are environment-only and remain on the self-hosted machine.
- AutoFly has no telemetry, central account, purchase capability, CAPTCHA bypass, stealth plugin, or proxy rotation. Its optional administration dashboard is disabled unless explicitly started.
- Flight GOAT and Playwright contact third-party fare sites; their behavior and terms can change.
- Webhooks require HTTPS and reject non-global destinations unless the administrator explicitly opts into trusted private networks.
- Subprocesses use argument arrays, timeouts, bounded reads, and no shell.
- Telegram controls accept commands only from the exact configured private chat ID. Group control
  is not supported, and the controller never removes an existing Telegram webhook automatically.

Administrators must protect `/etc/autofly/autofly.env`, the SQLite database, diagnostics, and any persistent browser profile; run as a dedicated non-root user and keep dependencies updated.

Keep the dashboard bound to loopback and reach it through SSH where possible. A non-loopback bind
requires an explicit flag and a password, but that does not provide transport encryption. Any
direct remote deployment must use an authenticated TLS reverse proxy and network access controls.
See [`docs/dashboard.md`](docs/dashboard.md).
