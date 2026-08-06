from __future__ import annotations

import importlib.util
import json
import os
import shutil
import time
import uuid
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any

import typer

from autofly import __version__
from autofly.config import AppConfig, load_config
from autofly.database import Database
from autofly.engine import WatchEngine
from autofly.errors import AutoFlyError, ConfigError
from autofly.locking import ProcessLock
from autofly.logging import configure_logging
from autofly.notifications.base import Notification, NotificationProvider
from autofly.notifications.telegram import TelegramNotifier
from autofly.notifications.webhook import WebhookNotifier
from autofly.scheduler import next_delay_seconds
from autofly.setup import (
    build_setup_config,
    parse_iata_list,
    parse_iso_date,
    write_setup_files,
)
from autofly.sources.flight_goat import FlightGoatSource
from autofly.sources.playwright import PlaywrightSource

app = typer.Typer(help="Self-hosted flight-fare monitoring.", no_args_is_help=True)
config_app = typer.Typer(help="Inspect and validate configuration.")
watches_app = typer.Typer(help="Inspect configured watches.")
app.add_typer(config_app, name="config")
app.add_typer(watches_app, name="watches")


def _config_path(path: Path | None) -> Path:
    return path or Path(os.environ.get("AUTOFLY_CONFIG", "config.yaml"))


def _load(path: Path | None) -> AppConfig:
    return load_config(_config_path(path))


def _database(config: AppConfig) -> Database:
    return Database(config.database.path, config.database.busy_timeout_seconds)


def _notifiers(config: AppConfig) -> list[NotificationProvider]:
    result: list[NotificationProvider] = []
    if config.notifications.telegram.enabled:
        result.append(TelegramNotifier(config.notifications.telegram))
    if config.notifications.webhook.enabled:
        result.append(WebhookNotifier(config.notifications.webhook))
    return result


def _source(config: AppConfig) -> Any:
    if config.sources.flight_goat.enabled:
        return FlightGoatSource(config.sources.flight_goat)
    if config.sources.playwright.enabled:
        return PlaywrightSource(config.sources.playwright)
    raise ConfigError("Enable at least one fare source")


def _emit(value: Any, json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(value, indent=2, sort_keys=True, default=str))
    elif isinstance(value, str):
        typer.echo(value)
    else:
        typer.echo(json.dumps(value, indent=2, default=str))


def _abort(exc: Exception, code: int = 2) -> None:
    typer.echo(f"Error: {exc}", err=True)
    raise typer.Exit(code) from exc


@app.command("init")
def init_command(
    path: Annotated[Path, typer.Option("--path", help="Configuration path.")] = Path("config.yaml"),
) -> None:
    """Generate starter configuration without overwriting existing files."""
    if path.exists():
        _abort(ConfigError(f"Refusing to overwrite existing {path}"))
    path.parent.mkdir(parents=True, exist_ok=True)
    template = files("autofly").joinpath("templates/config.yaml").read_text(encoding="utf-8")
    path.write_text(template, encoding="utf-8")
    typer.echo(f"Created {path}. Edit it, then run: autofly config validate --config {path}")


@app.command("setup")
def setup_command(
    config_path: Annotated[
        Path, typer.Option("--config", help="Configuration file to create.")
    ] = Path("config.yaml"),
    env_path: Annotated[Path, typer.Option("--env-file", help="Secret environment file.")] = Path(
        ".env"
    ),
    docker: Annotated[
        bool, typer.Option("--docker", help="Generate paths for Docker Compose.")
    ] = False,
) -> None:
    """Interactively create a validated first watch and environment file."""
    existing = [str(path) for path in (config_path, env_path) if path.exists()]
    if existing:
        _abort(ConfigError(f"Refusing to overwrite existing file(s): {', '.join(existing)}"))
    try:
        origins = parse_iata_list(typer.prompt("Origin IATA code(s), comma separated"), "origins")
        destinations = parse_iata_list(
            typer.prompt("Destination IATA code(s), comma separated"), "destinations"
        )
        date_mode = typer.prompt("Date mode", default="range").strip().lower()
        if date_mode == "exact":
            date_values: dict[str, Any] = {
                "departure": parse_iso_date(
                    typer.prompt("Departure date (YYYY-MM-DD)"), "departure"
                )
            }
        elif date_mode == "range":
            date_values = {
                "departure_start": parse_iso_date(
                    typer.prompt("First departure date (YYYY-MM-DD)"), "departure_start"
                ),
                "departure_end": parse_iso_date(
                    typer.prompt("Last departure date (YYYY-MM-DD)"), "departure_end"
                ),
            }
        elif date_mode == "rolling":
            date_values = {
                "days_from_now": typer.prompt("Start days from now", default=1, type=int),
                "days_to": typer.prompt("End days from now", default=30, type=int),
            }
        else:
            raise ConfigError("Date mode must be exact, range, or rolling")
        currency = typer.prompt("Currency", default="USD")
        maximum_price = typer.prompt("Maximum price", type=float)
        timezone = typer.prompt("IANA timezone", default="UTC")
        telegram_enabled = typer.confirm("Enable Telegram notifications?", default=True)
        token = typer.prompt("Telegram bot token", hide_input=True) if telegram_enabled else None
        chat_id = typer.prompt("Telegram chat ID") if telegram_enabled else None
        config = build_setup_config(
            origins=origins,
            destinations=destinations,
            date_mode=date_mode,
            date_values=date_values,
            currency=currency,
            maximum_price=maximum_price,
            timezone=timezone,
            telegram_enabled=telegram_enabled,
            docker=docker,
            config_path=config_path,
        )
        write_setup_files(
            config,
            config_path=config_path,
            env_path=env_path,
            telegram_token=token,
            telegram_chat_id=chat_id,
            docker=docker,
        )
    except (AutoFlyError, ValueError) as exc:
        _abort(exc)
    typer.echo(f"Created {config_path} and {env_path}.")
    typer.echo(f"Next: load {env_path}, then run autofly doctor --config {config_path}")


@config_app.command("validate")
def config_validate(
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        loaded = _load(config)
    except AutoFlyError as exc:
        _abort(exc)
    _emit(
        {
            "valid": True,
            "version": loaded.version,
            "enabled_watches": sum(w.enabled for w in loaded.watches),
        },
        json_output,
    )


@watches_app.command("list")
def watches_list(
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        loaded = _load(config)
    except AutoFlyError as exc:
        _abort(exc)
    rows = [
        {
            "id": watch.id,
            "enabled": watch.enabled,
            "routes": len(watch.route_pairs()),
            "date_mode": watch.dates.mode,
            "currency": watch.deal.currency,
            "maximum_price": watch.deal.maximum_price,
        }
        for watch in loaded.watches
    ]
    _emit(rows, json_output)


@app.command("routes")
def routes_command(
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        loaded = _load(config)
    except AutoFlyError as exc:
        _abort(exc)
    rows = [
        {"watch_id": watch.id, "origin": origin, "destination": destination}
        for watch in loaded.watches
        if watch.enabled
        for origin, destination in watch.route_pairs()
    ]
    _emit(rows, json_output)


@app.command("check")
def check_command(
    all_watches: Annotated[bool, typer.Option("--all", help="Check all enabled watches.")] = False,
    watch: Annotated[str | None, typer.Option("--watch", help="Check one watch ID.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run one fare-check cycle."""
    if all_watches and watch is not None:
        _abort(ConfigError("Choose only one of --all or --watch"))
    if not all_watches and watch is None:
        if dry_run:
            all_watches = True
        else:
            _abort(ConfigError("Choose --all or --watch"))
    try:
        loaded = _load(config)
        db = _database(loaded)
        source = _source(loaded)
        notifiers = [] if dry_run else _notifiers(loaded)
        engine = WatchEngine(loaded, db, source, notifiers)
        if dry_run:
            result = engine.run(None if all_watches else {watch or ""}, dry_run=True)
        else:
            with ProcessLock(loaded.scheduler.lock_path):
                result = engine.run(None if all_watches else {watch or ""})
        db.close()
    except AutoFlyError as exc:
        _abort(exc, 3)
    _emit(result, json_output)
    if result.get("status") not in {"success", "dry_run"}:
        raise typer.Exit(4)


@app.command("run")
def run_command(
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    """Continuously run all watches using the configured interval and jitter."""
    typer.echo("AutoFly scheduler started; press Ctrl+C to stop.")
    while True:
        try:
            loaded = _load(config)
            db = _database(loaded)
            try:
                with ProcessLock(loaded.scheduler.lock_path):
                    result = WatchEngine(loaded, db, _source(loaded), _notifiers(loaded)).run()
            finally:
                db.close()
            _emit(result, False)
        except AutoFlyError as exc:
            typer.echo(f"Cycle error: {exc}", err=True)
        try:
            loaded = _load(config)
            delay = next_delay_seconds(
                loaded.scheduler.interval_hours, loaded.scheduler.jitter_minutes
            )
        except AutoFlyError as exc:
            typer.echo(f"Configuration error: {exc}", err=True)
            delay = 300
        typer.echo(f"Next cycle in {delay / 3600:.2f} hours.")
        try:
            time.sleep(delay)
        except KeyboardInterrupt:
            typer.echo("AutoFly scheduler stopped.")
            return


@app.command("doctor")
def doctor_command(
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Check configuration, storage, source, notifier, and lock readiness."""
    checks: list[dict[str, str]] = []
    try:
        loaded = _load(config)
        checks.append({"check": "configuration", "status": "ok", "detail": "valid"})
        db = _database(loaded)
        db.connection.execute("SELECT 1").fetchone()
        db.close()
        checks.append({"check": "database", "status": "ok", "detail": str(loaded.database.path)})
        with ProcessLock(loaded.scheduler.lock_path):
            pass
        checks.append({"check": "process_lock", "status": "ok", "detail": "available"})
        if not loaded.sources.flight_goat.enabled and not loaded.sources.playwright.enabled:
            raise ConfigError("Enable at least one fare source")
        if loaded.sources.flight_goat.enabled:
            source_path = shutil.which(loaded.sources.flight_goat.command)
            if source_path is None and not Path(loaded.sources.flight_goat.command).is_file():
                raise ConfigError(
                    f"Flight GOAT executable not found: {loaded.sources.flight_goat.command}"
                )
            source = FlightGoatSource(loaded.sources.flight_goat)
            version_text = source.version()
            expected = loaded.sources.flight_goat.expected_version
            status = "ok" if not expected or expected in version_text else "warning"
            checks.append(
                {"check": "flight_goat", "status": status, "detail": version_text.strip()}
            )
            source.doctor()
        if loaded.sources.playwright.enabled:
            available = importlib.util.find_spec("playwright") is not None
            checks.append(
                {
                    "check": "playwright",
                    "status": "ok" if available else "error",
                    "detail": "installed" if available else "install autofly[playwright]",
                }
            )
        for name, enabled, variables in [
            (
                "telegram",
                loaded.notifications.telegram.enabled,
                [
                    loaded.notifications.telegram.bot_token_env,
                    loaded.notifications.telegram.chat_id_env,
                ],
            ),
            (
                "webhook",
                loaded.notifications.webhook.enabled,
                [loaded.notifications.webhook.url_env],
            ),
        ]:
            missing = [item for item in variables if enabled and not os.environ.get(item)]
            checks.append(
                {
                    "check": name,
                    "status": "error" if missing else ("ok" if enabled else "disabled"),
                    "detail": f"missing: {', '.join(missing)}" if missing else "ready",
                }
            )
    except Exception as exc:
        checks.append({"check": "doctor", "status": "error", "detail": str(exc)})
    _emit(checks, json_output)
    if any(item["status"] == "error" for item in checks):
        raise typer.Exit(2)


@app.command("notify-test")
def notify_test(
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
) -> None:
    try:
        loaded = _load(config)
        notifiers = _notifiers(loaded)
        if not notifiers:
            raise ConfigError("No notification providers are enabled")
        db = _database(loaded)
        cycle_id = db.start_cycle()
        notification = Notification(event="health_recovered", reason="AutoFly test notification")
        failures: list[str] = []
        for notifier in notifiers:
            key = f"autofly-notification-test-{uuid.uuid4()}"
            try:
                notifier.send(notification, key)
            except Exception as exc:
                failures.append(notifier.name)
                db.record_notification(
                    cycle_id=cycle_id,
                    watch_id="__health__",
                    itinerary_id=None,
                    provider=notifier.name,
                    reason="manual_test",
                    price=None,
                    status="failed",
                    idempotency_key=key,
                    error=f"{type(exc).__name__}: delivery failed",
                )
            else:
                db.record_notification(
                    cycle_id=cycle_id,
                    watch_id="__health__",
                    itinerary_id=None,
                    provider=notifier.name,
                    reason="manual_test",
                    price=None,
                    status="success",
                    idempotency_key=key,
                )
        db.finish_cycle(
            cycle_id,
            "failed" if failures else "success",
            {"notification_test": True, "failed_providers": failures},
        )
        db.close()
        if failures:
            raise ConfigError(f"Notification test failed for: {', '.join(failures)}")
    except AutoFlyError as exc:
        _abort(exc, 5)
    typer.echo(f"Sent test notification through {len(notifiers)} provider(s)")


@app.command("history")
def history_command(
    watch: Annotated[str | None, typer.Option("--watch")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=1000)] = 50,
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        loaded = _load(config)
        db = _database(loaded)
        rows = db.history(watch, limit)
        db.close()
    except AutoFlyError as exc:
        _abort(exc)
    _emit(rows, json_output)


@app.command("version")
def version_command() -> None:
    typer.echo(__version__)


def main() -> None:
    configure_logging()
    app()
