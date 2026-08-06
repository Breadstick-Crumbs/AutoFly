# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/) and semantic versioning.

## [Unreleased]

### Added

- Plain-language dashboard onboarding, setup-readiness indicators, and a live watch-rule preview.

### Changed

- Group the watch editor into guided route, travel, and deal sections with clearer field help.
- Clarify monitoring status, watch actions, fare-result labels, and search activity for new users.

## [0.2.0] - 2026-08-07

### Added

- Private, responsive web dashboard for watch management, fare history, source health, and manual checks.
- Optional HTTP Basic authentication and same-origin mutation protection for remote deployments.
- Atomic validated YAML updates with one-generation configuration backups.
- Loopback-only Docker Compose dashboard profile and SSH-tunnel deployment guidance.

### Changed

- Compose configuration now lives at `autofly-config/config.yaml`, allowing the dashboard to use
  atomic file replacement while the scheduler retains a read-only mount.

### Fixed

- Suppress HTTP transport request logging so token-bearing Telegram API URLs are never emitted.
- Report missing Flight GOAT executables without an unhandled traceback.
- Ignore isolated invalid source offers while rejecting wholly malformed result sets.

### Added

- Interactive `autofly setup` wizard with safe config and environment generation.
- Continuous `autofly run` scheduler for Docker deployments.
- Checksum-verified native Flight GOAT installer for Linux AMD64 and ARM64.
- Fresh-environment package smoke tests and multi-architecture Docker CI builds.
- Faster pinned Docker builds using the Flight GOAT Go module directly.

## [0.1.0] - 2026-08-06

### Added

- Validated multi-watch YAML configuration and exact, range, and rolling date strategies.
- Flight GOAT `2026.8.1` integration with pacing, retries, output limits, and 429 handling.
- SQLite history, itinerary identity, deal rules, cooldowns, and alert deduplication.
- Telegram and generic JSON webhooks, health alerts, CLI, process lock, and structured logs.
- Opt-in conservative Playwright exact-search fallback.
- Docker, systemd, cron, CI, security, deployment, and contributor documentation.
