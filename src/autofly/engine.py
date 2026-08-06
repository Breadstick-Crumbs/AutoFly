from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from autofly.config import AppConfig, ExactDates, WatchConfig
from autofly.database import Database, cooldown_elapsed
from autofly.dates import strategy_for
from autofly.deals import decide_alert, evaluate_offer
from autofly.errors import QueryBudgetExceeded, SourceError, SourceRateLimited
from autofly.logging import log_event
from autofly.models import FareOffer, SearchRequest
from autofly.notifications.base import Notification, NotificationProvider
from autofly.sources.base import FareSource

logger = logging.getLogger("autofly.engine")


@dataclass
class QueryBudget:
    limit: int
    used: int = 0

    def consume(self) -> None:
        if self.used >= self.limit:
            raise QueryBudgetExceeded(
                f"Per-cycle query budget exhausted ({self.used}/{self.limit}); "
                "raise scheduler.max_queries_per_cycle explicitly if intended"
            )
        self.used += 1


@dataclass
class CycleMetrics:
    routes_attempted: int = 0
    query_budget_usage: int = 0
    successful_searches: int = 0
    failed_searches: int = 0
    candidate_count: int = 0
    qualifying_count: int = 0
    notifications_sent: int = 0
    rate_limit_events: int = 0

    def as_dict(self) -> dict[str, int]:
        return vars(self)


class WatchEngine:
    def __init__(
        self,
        config: AppConfig,
        database: Database,
        source: FareSource,
        notifiers: list[NotificationProvider],
    ):
        self.config = config
        self.db = database
        self.source = source
        self.notifiers = notifiers

    def run(
        self,
        watch_ids: set[str] | None = None,
        *,
        dry_run: bool = False,
        today: date | None = None,
    ) -> dict[str, Any]:
        cycle_started = datetime.now(UTC)
        selected = [
            watch
            for watch in self.config.watches
            if watch.enabled and (watch_ids is None or watch.id in watch_ids)
        ]
        unknown = (watch_ids or set()) - {watch.id for watch in self.config.watches}
        if unknown:
            raise QueryBudgetExceeded(f"Unknown watch ID(s): {', '.join(sorted(unknown))}")
        if dry_run:
            return self._dry_run(selected, today or date.today())
        cycle_id = self.db.start_cycle()
        metrics = CycleMetrics()
        budget = QueryBudget(self.config.scheduler.max_queries_per_cycle)
        rate_limited = False
        budget_exceeded = False
        for watch in selected:
            self.db.snapshot_watch(cycle_id, watch.id, watch.model_dump(mode="json", by_alias=True))
            observed: set[str] = set()
            watch_failed = False
            for origin, destination in watch.route_pairs():
                metrics.routes_attempted += 1
                try:
                    offers = self._search_route(
                        cycle_id,
                        watch,
                        origin,
                        destination,
                        budget,
                        today or date.today(),
                        metrics,
                    )
                    metrics.candidate_count += len(offers)
                    for offer in offers:
                        observed.add(offer.itinerary_id)
                        self._process_offer(cycle_id, watch, offer, metrics)
                except SourceRateLimited as exc:
                    watch_failed = True
                    rate_limited = True
                    metrics.failed_searches += 1
                    metrics.rate_limit_events += 1
                    self.db.record_failure(
                        cycle_id, self.source.name, "rate_limit", str(exc), watch.id
                    )
                    break
                except (SourceError, QueryBudgetExceeded) as exc:
                    watch_failed = True
                    metrics.failed_searches += 1
                    self.db.record_failure(cycle_id, self.source.name, "search", str(exc), watch.id)
                    if isinstance(exc, QueryBudgetExceeded):
                        budget_exceeded = True
                        break
            if not watch_failed:
                self.db.mark_unseen_unavailable(watch.id, observed, cycle_started)
            if rate_limited or budget_exceeded:
                break
        metrics.query_budget_usage = budget.used
        success = metrics.failed_searches == 0
        if success:
            status = "success"
        elif rate_limited:
            status = "rate_limited"
        elif budget_exceeded:
            status = "budget_exceeded"
        else:
            status = "failed"
        self.db.finish_cycle(cycle_id, status, metrics.as_dict())
        unhealthy, recovered = self.db.update_health(success)
        if unhealthy:
            self._send_health(cycle_id, "health_unhealthy", "Three consecutive cycles failed")
        elif recovered:
            self._send_health(cycle_id, "health_recovered", "Fare checks are healthy again")
        output: dict[str, Any] = {"cycle_id": cycle_id, "status": status, **metrics.as_dict()}
        log_event(logger, "cycle_complete", **output)
        return output

    def _search_route(
        self,
        cycle_id: str,
        watch: WatchConfig,
        origin: str,
        destination: str,
        budget: QueryBudget,
        today: date,
        metrics: CycleMetrics,
    ) -> list[FareOffer]:
        if isinstance(watch.dates, ExactDates):
            request = self._request(watch, origin, destination, watch.dates.departure)
            return self._verified_search(cycle_id, request, budget, metrics)
        start, end = strategy_for(watch.dates).bounds(today)
        budget.consume()
        discovery_id = self.db.start_request_payload(
            cycle_id,
            watch.id,
            self.source.name,
            {
                "type": "date_discovery",
                "origin": origin,
                "destination": destination,
                "start": start,
                "end": end,
                "currency": watch.deal.currency,
            },
        )
        try:
            candidates = self.source.discover_dates(
                origin=origin,
                destination=destination,
                start=start,
                end=end,
                currency=watch.deal.currency,
                cabin=watch.trip.cabin,
                max_stops=watch.deal.max_stops,
            )
        except Exception as exc:
            self.db.finish_request(discovery_id, "failed", str(exc)[:1000])
            raise
        self.db.finish_request(discovery_id, "success")
        metrics.successful_searches += 1
        near_limit = Decimal(str(watch.deal.maximum_price)) * Decimal("1.10")
        candidates = sorted(
            (
                item
                for item in candidates
                if item.currency == watch.deal.currency and item.price < near_limit
            ),
            key=lambda item: item.price,
        )[: self.config.sources.flight_goat.max_verifications_per_route]
        offers: list[FareOffer] = []
        for candidate in candidates:
            request = self._request(watch, origin, destination, candidate.departure_date)
            offers.extend(self._verified_search(cycle_id, request, budget, metrics))
        return offers

    def _verified_search(
        self,
        cycle_id: str,
        request: SearchRequest,
        budget: QueryBudget,
        metrics: CycleMetrics,
    ) -> list[FareOffer]:
        budget.consume()
        request_id = self.db.start_request(cycle_id, request, self.source.name)
        try:
            offers = self.source.search(request)
        except Exception as exc:
            self.db.finish_request(request_id, "failed", str(exc)[:1000])
            raise
        self.db.finish_request(request_id, "success")
        metrics.successful_searches += 1
        return offers

    def _request(
        self, watch: WatchConfig, origin: str, destination: str, departure: date
    ) -> SearchRequest:
        return_date = watch.dates.return_date if isinstance(watch.dates, ExactDates) else None
        return SearchRequest(
            watch_id=watch.id,
            origin=origin,
            destination=destination,
            departure_date=departure,
            return_date=return_date,
            trip_type=watch.trip.type,
            cabin=watch.trip.cabin,
            adults=watch.trip.adults,
            currency=watch.deal.currency,
            max_stops=watch.deal.max_stops,
            max_layover_minutes=int(watch.deal.max_layover_hours * 60)
            if watch.deal.max_layover_hours is not None
            else None,
        )

    def _process_offer(
        self, cycle_id: str, watch: WatchConfig, offer: FareOffer, metrics: CycleMetrics
    ) -> None:
        state = self.db.observe(cycle_id, watch.id, offer)
        if not evaluate_offer(offer, watch).qualifies:
            return
        metrics.qualifying_count += 1
        for notifier in self.notifiers:
            previous = self.db.last_successful_alert(watch.id, offer.itinerary_id, notifier.name)
            previous_price, previous_at = previous if previous else (None, None)
            reappeared = bool(
                state.was_unavailable
                and previous_at
                and cooldown_elapsed(
                    previous_at, watch.notifications.cooldown_hours, datetime.now(UTC)
                )
            )
            decision = decide_alert(
                current_price=offer.price,
                last_alert_price=previous_price,
                reappeared_after_cooldown=reappeared,
                watch=watch,
            )
            if not decision.send:
                continue
            notification = Notification(
                event="deal",
                watch_id=watch.id,
                reason=decision.reason,
                offer=offer,
                previous_price=state.previous_price,
            )
            key = _notification_key(watch.id, offer, decision.reason)
            try:
                notifier.send(notification, key)
            except Exception as exc:
                self.db.record_notification(
                    cycle_id=cycle_id,
                    watch_id=watch.id,
                    itinerary_id=offer.itinerary_id,
                    provider=notifier.name,
                    reason=decision.reason,
                    price=offer.price,
                    status="failed",
                    idempotency_key=key,
                    error=str(exc)[:500],
                )
            else:
                self.db.record_notification(
                    cycle_id=cycle_id,
                    watch_id=watch.id,
                    itinerary_id=offer.itinerary_id,
                    provider=notifier.name,
                    reason=decision.reason,
                    price=offer.price,
                    status="success",
                    idempotency_key=key,
                )
                metrics.notifications_sent += 1

    def _send_health(self, cycle_id: str, event: str, reason: str) -> None:
        notification = Notification(event=event, reason=reason)  # type: ignore[arg-type]
        key = hashlib.sha256(f"{event}:{cycle_id}".encode()).hexdigest()
        for notifier in self.notifiers:
            try:
                notifier.send(notification, key)
            except Exception as exc:
                self.db.record_notification(
                    cycle_id=cycle_id,
                    watch_id="__health__",
                    itinerary_id=None,
                    provider=notifier.name,
                    reason=reason,
                    price=None,
                    status="failed",
                    idempotency_key=key,
                    error=str(exc)[:500],
                )
            else:
                self.db.record_notification(
                    cycle_id=cycle_id,
                    watch_id="__health__",
                    itinerary_id=None,
                    provider=notifier.name,
                    reason=reason,
                    price=None,
                    status="success",
                    idempotency_key=key,
                )

    def _dry_run(self, watches: list[WatchConfig], today: date) -> dict[str, Any]:
        routes = []
        for watch in watches:
            start, end = strategy_for(watch.dates).bounds(today)
            for origin, destination in watch.route_pairs():
                routes.append(
                    {
                        "watch_id": watch.id,
                        "origin": origin,
                        "destination": destination,
                        "date_mode": watch.dates.mode,
                        "from": start.isoformat(),
                        "to": end.isoformat(),
                        "return": watch.dates.return_date.isoformat()
                        if isinstance(watch.dates, ExactDates) and watch.dates.return_date
                        else None,
                    }
                )
        return {"status": "dry_run", "routes": routes, "queries_executed": 0}


def _notification_key(watch_id: str, offer: FareOffer, reason: str) -> str:
    raw = f"v1:{watch_id}:{offer.itinerary_id}:{reason}:{offer.price}:{offer.currency}"
    return hashlib.sha256(raw.encode()).hexdigest()
