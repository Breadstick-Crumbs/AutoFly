# Security policy

## Supported versions

Until a stable release, only the latest tagged `0.x` release and current default branch receive security fixes.

## Reporting

Do not open a public issue for a vulnerability. Use GitHub's private vulnerability reporting for this repository when available, or contact the repository owner privately. Include affected versions, impact, reproduction steps, and any suggested mitigation. Allow maintainers reasonable time to investigate before disclosure.

Never include real bot tokens, webhook URLs, cookies, browser profiles, or route history in a report. Redact query parameters and authentication headers.

## Security model

- Secrets are environment-only and remain on the self-hosted machine.
- AutoFly has no telemetry, central account, inbound server, purchase capability, CAPTCHA bypass, stealth plugin, or proxy rotation.
- Flight GOAT and Playwright contact third-party fare sites; their behavior and terms can change.
- Webhooks require HTTPS and reject non-global destinations unless the administrator explicitly opts into trusted private networks.
- Subprocesses use argument arrays, timeouts, bounded reads, and no shell.

Administrators must protect `/etc/autofly/autofly.env`, the SQLite database, diagnostics, and any persistent browser profile; run as a dedicated non-root user and keep dependencies updated.

