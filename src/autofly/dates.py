from __future__ import annotations

from datetime import date, timedelta
from typing import Protocol

from autofly.config import ExactDates, RangeDates, RollingDates


class DateStrategy(Protocol):
    def bounds(self, today: date) -> tuple[date, date]: ...

    def dates(self, today: date) -> list[date]: ...


class ExactDateStrategy:
    def __init__(self, config: ExactDates):
        self.config = config

    def bounds(self, today: date) -> tuple[date, date]:
        return self.config.departure, self.config.departure

    def dates(self, today: date) -> list[date]:
        return [self.config.departure]


class RangeDateStrategy:
    def __init__(self, config: RangeDates):
        self.config = config

    def bounds(self, today: date) -> tuple[date, date]:
        return self.config.departure_start, self.config.departure_end

    def dates(self, today: date) -> list[date]:
        days = (self.config.departure_end - self.config.departure_start).days
        return [self.config.departure_start + timedelta(days=i) for i in range(days + 1)]


class RollingDateStrategy:
    def __init__(self, config: RollingDates):
        self.config = config

    def bounds(self, today: date) -> tuple[date, date]:
        return (
            today + timedelta(days=self.config.days_from_now),
            today + timedelta(days=self.config.days_to),
        )

    def dates(self, today: date) -> list[date]:
        start, end = self.bounds(today)
        return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def strategy_for(config: ExactDates | RangeDates | RollingDates) -> DateStrategy:
    if isinstance(config, ExactDates):
        return ExactDateStrategy(config)
    if isinstance(config, RangeDates):
        return RangeDateStrategy(config)
    return RollingDateStrategy(config)
