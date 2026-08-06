from __future__ import annotations

import shutil
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autofly.config import AppConfig, WatchConfig, load_config
from autofly.database import Database
from autofly.engine import WatchEngine
from autofly.errors import ConfigError
from autofly.locking import ProcessLock
from autofly.notifications.base import NotificationProvider
from autofly.notifications.telegram import TelegramNotifier
from autofly.notifications.webhook import WebhookNotifier
from autofly.sources.base import FareSource
from autofly.sources.flight_goat import FlightGoatSource
from autofly.sources.playwright import PlaywrightSource

from .config_store import ConfigStore


@dataclass
class CheckJob:
    id: str
    watch_id: str | None
    status: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


class DashboardService:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.config_store = ConfigStore(config_path)
        self._jobs: dict[str, CheckJob] = {}
        self._job_lock = threading.Lock()

    def state(self) -> dict[str, Any]:
        config = load_config(self.config_path)
        database = Database(config.database.path, config.database.busy_timeout_seconds)
        try:
            summary = database.dashboard_summary()
            cycles = database.recent_cycles(8)
        finally:
            database.close()
        source_command = config.sources.flight_goat.command
        source_available = bool(shutil.which(source_command) or Path(source_command).is_file())
        with self._job_lock:
            jobs = [asdict(job) for job in reversed(list(self._jobs.values())[-10:])]
        return {
            "version": config.version,
            "scheduler": config.scheduler.model_dump(mode="json"),
            "source": {
                "name": "flight_goat" if config.sources.flight_goat.enabled else "playwright",
                "available": source_available if config.sources.flight_goat.enabled else True,
                "expected_version": config.sources.flight_goat.expected_version,
            },
            "notifications": {
                "telegram": config.notifications.telegram.enabled,
                "webhook": config.notifications.webhook.enabled,
            },
            "watches": [watch.model_dump(mode="json", by_alias=True) for watch in config.watches],
            "summary": summary,
            "cycles": cycles,
            "jobs": jobs,
        }

    def history(
        self,
        watch_id: str | None,
        limit: int,
        *,
        qualifying: bool | None = None,
        origin: str | None = None,
        destination: str | None = None,
        max_stops: int | None = None,
        airline: str | None = None,
    ) -> list[dict[str, Any]]:
        config = load_config(self.config_path)
        database = Database(config.database.path, config.database.busy_timeout_seconds)
        try:
            return database.dashboard_history(
                watch_id,
                limit,
                qualifying=qualifying,
                origin=origin,
                destination=destination,
                max_stops=max_stops,
                airline=airline,
            )
        finally:
            database.close()

    def trend(
        self, watch_id: str, origin: str, destination: str, limit: int
    ) -> list[dict[str, Any]]:
        config = load_config(self.config_path)
        if watch_id not in {watch.id for watch in config.watches}:
            raise ConfigError(f"Unknown watch ID: {watch_id}")
        database = Database(config.database.path, config.database.busy_timeout_seconds)
        try:
            return database.dashboard_trend(watch_id, origin, destination, limit)
        finally:
            database.close()

    def delivery_status(self, limit: int) -> dict[str, list[dict[str, Any]]]:
        config = load_config(self.config_path)
        database = Database(config.database.path, config.database.busy_timeout_seconds)
        try:
            return {
                "notifications": database.dashboard_notifications(limit),
                "source_failures": database.dashboard_failures(limit),
            }
        finally:
            database.close()

    def save_watch(self, watch: WatchConfig, original_id: str | None) -> AppConfig:
        return self.config_store.save_watch(watch, original_id=original_id)

    def set_enabled(self, watch_id: str, enabled: bool) -> AppConfig:
        return self.config_store.set_enabled(watch_id, enabled)

    def start_check(self, watch_id: str | None) -> CheckJob:
        config = load_config(self.config_path)
        if watch_id is not None and watch_id not in {watch.id for watch in config.watches}:
            raise ConfigError(f"Unknown watch ID: {watch_id}")
        with self._job_lock:
            if any(job.status in {"queued", "running"} for job in self._jobs.values()):
                raise ConfigError("A manual check is already running")
            job = CheckJob(
                id=str(uuid.uuid4()),
                watch_id=watch_id,
                status="queued",
                created_at=_now(),
            )
            self._jobs[job.id] = job
        thread = threading.Thread(target=self._run_job, args=(job.id,), daemon=True)
        thread.start()
        return job

    def job(self, job_id: str) -> CheckJob:
        with self._job_lock:
            try:
                return self._jobs[job_id]
            except KeyError as exc:
                raise ConfigError(f"Unknown check job: {job_id}") from exc

    def _run_job(self, job_id: str) -> None:
        with self._job_lock:
            job = self._jobs[job_id]
            job.status = "running"
            job.started_at = _now()
        database: Database | None = None
        try:
            config = load_config(self.config_path)
            database = Database(config.database.path, config.database.busy_timeout_seconds)
            engine = WatchEngine(config, database, _source(config), _notifiers(config))
            selected = {job.watch_id} if job.watch_id else None
            with ProcessLock(config.scheduler.lock_path):
                result = engine.run(selected)
        except Exception as exc:
            with self._job_lock:
                job.status = "failed"
                job.error = f"{type(exc).__name__}: {exc}"[:1000]
                job.finished_at = _now()
        else:
            with self._job_lock:
                job.status = "completed"
                job.result = result
                job.finished_at = _now()
        finally:
            if database is not None:
                database.close()


def _source(config: AppConfig) -> FareSource:
    if config.sources.flight_goat.enabled:
        return FlightGoatSource(config.sources.flight_goat)
    if config.sources.playwright.enabled:
        return PlaywrightSource(config.sources.playwright)
    raise ConfigError("Enable at least one fare source")


def _notifiers(config: AppConfig) -> list[NotificationProvider]:
    result: list[NotificationProvider] = []
    if config.notifications.telegram.enabled:
        result.append(TelegramNotifier(config.notifications.telegram))
    if config.notifications.webhook.enabled:
        result.append(WebhookNotifier(config.notifications.webhook))
    return result


def _now() -> str:
    return datetime.now(UTC).isoformat()
