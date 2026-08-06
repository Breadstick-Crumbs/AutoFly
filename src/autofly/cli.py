from __future__ import annotations

import importlib.util
import json
import os
import shutil
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
from autofly.sources.flight_goat import FlightGoatSource

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
    if all_watches == (watch is not None):
        _abort(ConfigError("Choose exactly one of --all or --watch"))
    try:
        loaded = _load(config)
        db = _database(loaded)
        source = FlightGoatSource(loaded.sources.flight_goat)
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
        source_path = shutil.which(loaded.sources.flight_goat.command)
        if source_path is None and not Path(loaded.sources.flight_goat.command).is_file():
            raise ConfigError(
                f"Flight GOAT executable not found: {loaded.sources.flight_goat.command}"
            )
        source = FlightGoatSource(loaded.sources.flight_goat)
        version_text = source.version()
        expected = loaded.sources.flight_goat.expected_version
        status = "ok" if not expected or expected in version_text else "warning"
        checks.append({"check": "flight_goat", "status": status, "detail": version_text.strip()})
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
        notification = Notification(event="health_recovered", reason="AutoFly test notification")
        for notifier in notifiers:
            notifier.send(notification, "autofly-notification-test")
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
