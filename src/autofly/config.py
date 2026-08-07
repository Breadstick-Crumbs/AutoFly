from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from autofly.errors import ConfigError
from autofly.models import Cabin, TripType

IATA_RE = re.compile(r"^[A-Z]{3}$")
WATCH_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class DatabaseConfig(BaseModel):
    path: Path = Path("./data/autofly.db")
    busy_timeout_seconds: float = Field(default=10, ge=1, le=60)


class SchedulerConfig(BaseModel):
    interval_hours: float = Field(default=6, gt=0)
    jitter_minutes: int = Field(default=10, ge=0, le=1440)
    timezone: str = "UTC"
    max_queries_per_cycle: int = Field(default=100, ge=1, le=10000)
    lock_path: Path = Path("./data/autofly.lock")

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("must be a valid IANA timezone") from exc
        return value


class FlightGoatConfig(BaseModel):
    enabled: bool = True
    command: str = "flight-goat-pp-cli"
    pace_seconds: float = Field(default=5, ge=2, le=300)
    timeout_seconds: float = Field(default=90, ge=5, le=600)
    max_output_bytes: int = Field(default=5_000_000, ge=1024, le=50_000_000)
    expected_version: str | None = "2026.8.1"
    max_retries: int = Field(default=2, ge=0, le=5)
    max_verifications_per_route: int = Field(default=5, ge=1, le=30)

    @field_validator("command")
    @classmethod
    def command_is_executable_name_or_path(cls, value: str) -> str:
        if not value.strip() or any(char in value for char in "\n\r\0"):
            raise ValueError("must be an executable name or path")
        return value.strip()


class PlaywrightConfig(BaseModel):
    enabled: bool = False
    headless: bool = True
    profile_path: Path = Path("./data/browser-profile")
    locale: str = "en-US"
    currency: str = "USD"
    diagnostic_path: Path = Path("./diagnostics")
    timeout_seconds: float = Field(default=45, ge=5, le=180)
    max_response_bytes: int = Field(default=5_000_000, ge=1024, le=50_000_000)


class SourcesConfig(BaseModel):
    flight_goat: FlightGoatConfig = Field(default_factory=FlightGoatConfig)
    playwright: PlaywrightConfig = Field(default_factory=PlaywrightConfig)


class TelegramConfig(BaseModel):
    enabled: bool = False
    control_enabled: bool = False
    bot_token_env: str = "TELEGRAM_BOT_TOKEN"  # noqa: S105 - environment variable name
    chat_id_env: str = "TELEGRAM_CHAT_ID"
    timeout_seconds: float = Field(default=15, ge=1, le=60)
    poll_timeout_seconds: int = Field(default=25, ge=5, le=50)

    @model_validator(mode="after")
    def controls_require_notifications(self) -> TelegramConfig:
        if self.control_enabled and not self.enabled:
            raise ValueError("control_enabled requires enabled: true")
        return self


class WebhookConfig(BaseModel):
    enabled: bool = False
    url_env: str = "AUTOFLY_WEBHOOK_URL"
    allow_private_networks: bool = False
    timeout_seconds: float = Field(default=15, ge=1, le=60)


class NotificationsConfig(BaseModel):
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    webhook: WebhookConfig = Field(default_factory=WebhookConfig)


class TripConfig(BaseModel):
    type: TripType = "one_way"
    adults: int = Field(default=1, ge=1, le=9)
    cabin: Cabin = "economy"


class ExactDates(BaseModel):
    mode: Literal["exact"]
    departure: date
    return_date: date | None = Field(default=None, alias="return")

    @model_validator(mode="after")
    def chronological(self) -> ExactDates:
        if self.return_date and self.return_date <= self.departure:
            raise ValueError("return must be after departure")
        return self


class RangeDates(BaseModel):
    mode: Literal["range"]
    departure_start: date
    departure_end: date

    @model_validator(mode="after")
    def chronological(self) -> RangeDates:
        if self.departure_end < self.departure_start:
            raise ValueError("departure_end must be on or after departure_start")
        if (self.departure_end - self.departure_start).days > 366:
            raise ValueError("range may not exceed 367 calendar days")
        return self


class RollingDates(BaseModel):
    mode: Literal["rolling"]
    days_from_now: int = Field(default=1, ge=0, le=730)
    days_to: int = Field(ge=0, le=730)

    @model_validator(mode="after")
    def chronological(self) -> RollingDates:
        if self.days_to < self.days_from_now:
            raise ValueError("days_to must be >= days_from_now")
        return self


DateConfig = Annotated[ExactDates | RangeDates | RollingDates, Field(discriminator="mode")]


class PriceDropConfig(BaseModel):
    amount: float | None = Field(default=None, gt=0)
    percentage: float | None = Field(default=None, gt=0, le=100)

    @model_validator(mode="after")
    def at_least_one_rule(self) -> PriceDropConfig:
        if self.amount is None and self.percentage is None:
            raise ValueError("amount or percentage is required")
        return self


class WatchNotificationConfig(BaseModel):
    cooldown_hours: float = Field(default=24, ge=0)
    alert_on_price_drop: PriceDropConfig = Field(default_factory=lambda: PriceDropConfig(amount=1))


class DealConfig(BaseModel):
    currency: str
    maximum_price: float = Field(gt=0)
    max_stops: int | None = Field(default=None, ge=0, le=3)
    max_layover_hours: float | None = Field(default=None, ge=0, le=72)
    allow_self_transfer: bool = False

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, value: str) -> str:
        value = value.upper()
        if not re.fullmatch(r"[A-Z]{3}", value):
            raise ValueError("must be a three-letter ISO 4217 code")
        return value


class WatchConfig(BaseModel):
    id: str
    enabled: bool = True
    origins: list[str]
    destinations: list[str]
    trip: TripConfig = Field(default_factory=TripConfig)
    dates: DateConfig
    deal: DealConfig
    notifications: WatchNotificationConfig = Field(default_factory=WatchNotificationConfig)

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if not WATCH_ID_RE.fullmatch(value):
            raise ValueError("must match ^[a-z0-9][a-z0-9_-]{0,63}$")
        return value

    @field_validator("origins", "destinations")
    @classmethod
    def valid_locations(cls, values: list[str]) -> list[str]:
        normalized = [v.upper() for v in values]
        if not normalized:
            raise ValueError("must contain at least one IATA identifier")
        invalid = [v for v in normalized if not IATA_RE.fullmatch(v)]
        if invalid:
            raise ValueError(f"invalid IATA identifier(s): {', '.join(invalid)}")
        if len(normalized) != len(set(normalized)):
            raise ValueError("must not contain duplicates")
        return normalized

    @model_validator(mode="after")
    def valid_trip_dates_and_routes(self) -> WatchConfig:
        same = sorted(set(self.origins) & set(self.destinations))
        if same:
            raise ValueError(f"origin equals destination: {', '.join(same)}")
        if self.trip.type == "round_trip":
            if not isinstance(self.dates, ExactDates):
                raise ValueError("flexible round trips are not supported in v0.1; use exact dates")
            if self.dates.return_date is None:
                raise ValueError("exact round trips require dates.return")
        elif isinstance(self.dates, ExactDates) and self.dates.return_date is not None:
            raise ValueError("one-way watches must not set dates.return")
        return self

    def route_pairs(self) -> list[tuple[str, str]]:
        return [(origin, dest) for origin in self.origins for dest in self.destinations]


class AppConfig(BaseModel):
    version: Literal[1]
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    watches: list[WatchConfig]

    @model_validator(mode="after")
    def unique_watches_and_safe_routes(self) -> AppConfig:
        ids = [watch.id for watch in self.watches]
        duplicates = sorted({item for item in ids if ids.count(item) > 1})
        if duplicates:
            raise ValueError(f"duplicate watch IDs: {', '.join(duplicates)}")
        estimated_queries = sum(
            len(watch.route_pairs())
            * (
                1
                if isinstance(watch.dates, ExactDates)
                else 1 + self.sources.flight_goat.max_verifications_per_route
            )
            for watch in self.watches
            if watch.enabled
        )
        if estimated_queries > self.scheduler.max_queries_per_cycle:
            raise ValueError(
                f"worst-case query count {estimated_queries} exceeds "
                "scheduler.max_queries_per_cycle "
                f"({self.scheduler.max_queries_per_cycle}); explicitly raise the limit"
            )
        return self


def _validation_message(exc: ValidationError, raw: object) -> str:
    watches = raw.get("watches", []) if isinstance(raw, dict) else []
    lines: list[str] = []
    for error in exc.errors(include_url=False):
        loc = list(error["loc"])
        watch_id = None
        if len(loc) > 1 and loc[0] == "watches" and isinstance(loc[1], int):
            index = loc[1]
            if index < len(watches) and isinstance(watches[index], dict):
                watch_id = watches[index].get("id", f"index {index}")
        field = ".".join(str(part) for part in loc)
        prefix = f"watch {watch_id!r}: " if watch_id is not None else ""
        lines.append(f"{prefix}{field}: {error['msg']}")
    return "Invalid AutoFly configuration:\n- " + "\n- ".join(lines)


def load_config(path: Path) -> AppConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"Cannot read configuration {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"Configuration {path} must contain a YAML mapping")
    overrides = {
        "AUTOFLY_DATABASE_PATH": ("database", "path"),
        "AUTOFLY_MAX_QUERIES_PER_CYCLE": ("scheduler", "max_queries_per_cycle"),
        "AUTOFLY_LOCK_PATH": ("scheduler", "lock_path"),
        "AUTOFLY_FLIGHT_GOAT_COMMAND": ("sources", "flight_goat", "command"),
    }
    for env_name, keys in overrides.items():
        if env_name not in os.environ:
            continue
        target = raw
        for key in keys[:-1]:
            target = target.setdefault(key, {})
        target[keys[-1]] = os.environ[env_name]
    return validate_config_data(raw)


def validate_config_data(raw: object) -> AppConfig:
    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(_validation_message(exc, raw)) from exc
