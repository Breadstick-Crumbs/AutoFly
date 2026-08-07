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

## v0.2 phase 3: dashboard usability

- [x] Replace operator jargon with plain-language monitoring and fare explanations.
- [x] Add a concise onboarding guide and live setup-readiness indicators.
- [x] Group watch creation into route, travel, and deal sections with field guidance.
- [x] Add a live watch-rule preview and clearer validation messages.
- [x] Clarify watch states, actions, fare results, and search activity.
- [x] Complete browser, accessibility, responsive, and regression validation; release and deploy.

## v0.2 phase 4: results and trust

- [x] Persist deal qualification and human-readable rejection reasons with a forward-only migration.
- [x] Add fare filters, deal-only results, and per-route lowest-price trends.
- [x] Surface notification delivery and sanitized source-failure status.
- [x] Keep pre-migration observations explicitly marked as not evaluated.
- [x] Add manual CI dispatch and explicit all-branch push triggers.
- [x] Complete migration, browser, package, CI, release, and live deployment validation.

## v0.2 phase 5: Telegram control center

- [x] Add a private chat-ID-bound command and inline-button interface.
- [x] Add guided creation and editing for complete watch rules.
- [x] Add pause, resume, delete, search, recent-deal, and status actions.
- [x] Reuse configuration validation, atomic backups, process locking, and query budgets.
- [x] Add Compose, systemd, security, and operator documentation.
- [x] Complete offline, package, deployment, and live Telegram interaction validation.
