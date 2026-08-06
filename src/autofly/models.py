from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator

TripType = Literal["one_way", "round_trip"]
Cabin = Literal["economy", "premium_economy", "business", "first"]


class SearchRequest(BaseModel):
    watch_id: str
    origin: str
    destination: str
    departure_date: date
    return_date: date | None = None
    trip_type: TripType = "one_way"
    cabin: Cabin = "economy"
    adults: int = Field(default=1, ge=1, le=9)
    currency: str
    max_stops: int | None = Field(default=None, ge=0, le=3)
    max_layover_minutes: int | None = Field(default=None, ge=0)


class FareOffer(BaseModel):
    source: str
    origin: str
    destination: str
    departure_at: datetime
    return_date: date | None = None
    return_at: datetime | None = None
    airline: str | None = None
    flight_numbers: list[str] = Field(default_factory=list)
    stops: int | None = None
    layover_minutes: list[int] | None = None
    duration_minutes: int | None = None
    trip_type: TripType
    cabin: Cabin
    passenger_count: int = Field(ge=1)
    price: Decimal = Field(gt=0)
    currency: str
    booking_url: HttpUrl
    self_transfer: bool | None = None
    baggage: str | None = None
    raw_reference: str | None = None
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("origin", "destination", "currency")
    @classmethod
    def uppercase(cls, value: str) -> str:
        return value.upper()

    @property
    def itinerary_id(self) -> str:
        """Stable identity intentionally excludes price and observation time."""
        identity = {
            "source": self.source,
            "origin": self.origin,
            "destination": self.destination,
            "departure_at": self.departure_at.isoformat(),
            "return_date": self.return_date.isoformat() if self.return_date else None,
            "return_at": self.return_at.isoformat() if self.return_at else None,
            "flight_numbers": self.flight_numbers,
            "trip_type": self.trip_type,
            "cabin": self.cabin,
            "passengers": self.passenger_count,
        }
        encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def safe_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
