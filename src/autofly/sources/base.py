from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel

from autofly.models import FareOffer, SearchRequest


class DateCandidate(BaseModel):
    departure_date: date
    return_date: date | None = None
    price: Decimal
    currency: str


class FareSource(Protocol):
    name: str

    def search(self, request: SearchRequest) -> list[FareOffer]: ...

    def discover_dates(
        self,
        *,
        origin: str,
        destination: str,
        start: date,
        end: date,
        currency: str,
        cabin: str,
        max_stops: int | None,
    ) -> list[DateCandidate]: ...
