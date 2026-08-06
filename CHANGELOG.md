# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/) and semantic versioning.

## [Unreleased]

### Fixed

- Suppress HTTP transport request logging so token-bearing Telegram API URLs are never emitted.

## [0.1.0] - 2026-08-06

### Added

- Validated multi-watch YAML configuration and exact, range, and rolling date strategies.
- Flight GOAT `2026.8.1` integration with pacing, retries, output limits, and 429 handling.
- SQLite history, itinerary identity, deal rules, cooldowns, and alert deduplication.
- Telegram and generic JSON webhooks, health alerts, CLI, process lock, and structured logs.
- Opt-in conservative Playwright exact-search fallback.
- Docker, systemd, cron, CI, security, deployment, and contributor documentation.
