from __future__ import annotations

from decimal import Decimal
from typing import Literal, Protocol

from pydantic import BaseModel

from autofly.models import FareOffer


class Notification(BaseModel):
    event: Literal["deal", "health_unhealthy", "health_recovered"]
    watch_id: str | None = None
    reason: str
    offer: FareOffer | None = None
    previous_price: Decimal | None = None


class NotificationProvider(Protocol):
    name: str

    def send(self, notification: Notification, idempotency_key: str) -> None: ...
