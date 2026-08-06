# AutoFly v0.1 execution plan

- [x] Inspect repository, upstream Flight GOAT documentation/source, and licensing.
- [x] Establish the Python package, configuration models, and date/route strategies.
- [x] Add versioned SQLite persistence, deal evaluation, and alert deduplication.
- [x] Integrate Flight GOAT safely and add an opt-in Playwright fallback.
- [x] Add Telegram/webhook notification providers and the CLI/cycle runner.
- [x] Add deployment assets, contributor documentation, CI, and security policy.
- [x] Run offline validation and package smoke tests; commit and push the branch. Docker build is delegated to CI because Docker is unavailable on the development host.

This file is intentionally short and is updated as milestones complete.

## v0.2 phase 1: five-minute self-hosting

- [x] Add a non-destructive interactive setup wizard.
- [x] Add a continuous scheduler for an unattended Compose service.
- [x] Pin and checksum the native AMD64/ARM64 Flight GOAT toolchain installation.
- [x] Add clean-environment and multi-architecture CI coverage.
- [x] Document first install, updates, rollback, and operational checks.

## v0.2 phase 2: private web dashboard

- [x] Add authenticated dashboard APIs and read-only operational summaries.
- [x] Add validated, atomic watch creation and editing with rollback backup.
- [x] Add serialized manual check jobs using the existing process lock and query budget.
- [x] Build a dependency-light responsive interface with no external browser assets.
- [x] Add a loopback-only Compose profile and secure remote-access guidance.
- [x] Complete final browser, Docker, package, and full-suite validation.
- [x] Push the reviewed feature branch for merge into `main`.
