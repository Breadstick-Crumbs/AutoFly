from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from autofly.config import WatchConfig
from autofly.models import FareOffer


@dataclass(frozen=True)
class DealResult:
    qualifies: bool
    reason: str


@dataclass(frozen=True)
class AlertDecision:
    send: bool
    reason: Literal["first_qualified", "price_drop", "reappeared", "unchanged"]


def evaluate_offer(offer: FareOffer, watch: WatchConfig) -> DealResult:
    if offer.origin not in watch.origins or offer.destination not in watch.destinations:
        return DealResult(False, "route does not match watch")
    if offer.currency != watch.deal.currency:
        return DealResult(False, "currency mismatch; AutoFly does not convert currency")
    if offer.price >= Decimal(str(watch.deal.maximum_price)):
        return DealResult(False, "price is not strictly below maximum_price")
    if watch.deal.max_stops is not None and (
        offer.stops is None or offer.stops > watch.deal.max_stops
    ):
        return DealResult(False, "stop limit exceeded or unknown")
    if watch.deal.max_layover_hours is not None:
        limit = int(watch.deal.max_layover_hours * 60)
        if offer.layover_minutes is None or any(item > limit for item in offer.layover_minutes):
            return DealResult(False, "layover limit exceeded or unknown")
    if not watch.deal.allow_self_transfer and offer.self_transfer is not False:
        return DealResult(False, "self-transfer status is true or unknown")
    if not str(offer.booking_url).startswith(("https://", "http://")):
        return DealResult(False, "no usable search link")
    return DealResult(True, "qualified")


def decide_alert(
    *,
    current_price: Decimal,
    last_alert_price: Decimal | None,
    reappeared_after_cooldown: bool,
    watch: WatchConfig,
) -> AlertDecision:
    if last_alert_price is None:
        return AlertDecision(True, "first_qualified")
    drop = last_alert_price - current_price
    percentage = (drop / last_alert_price * 100) if last_alert_price else Decimal(0)
    rule = watch.notifications.alert_on_price_drop
    amount_met = rule.amount is not None and drop >= Decimal(str(rule.amount))
    percentage_met = rule.percentage is not None and percentage >= Decimal(str(rule.percentage))
    if drop > 0 and (amount_met or percentage_met):
        return AlertDecision(True, "price_drop")
    if reappeared_after_cooldown:
        return AlertDecision(True, "reappeared")
    return AlertDecision(False, "unchanged")
