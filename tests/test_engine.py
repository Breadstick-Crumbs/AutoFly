from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from autofly.config import AppConfig
from autofly.database import Database
from autofly.engine import WatchEngine
from autofly.errors import SourceError
from autofly.models import FareOffer, SearchRequest
from autofly.notifications.base import Notification
from autofly.sources.base import DateCandidate


def config(*, rolling: bool = False, budget: int = 10) -> AppConfig:
    dates = (
        {"mode": "rolling", "days_from_now": 1, "days_to": 3}
        if rolling
        else {"mode": "exact", "departure": "2026-09-15"}
    )
    return AppConfig.model_validate(
        {
            "version": 1,
            "scheduler": {"max_queries_per_cycle": budget},
            "sources": {"flight_goat": {"max_verifications_per_route": 2}},
            "watches": [
                {
                    "id": "sample",
                    "origins": ["COK"],
                    "destinations": ["DXB"],
                    "dates": dates,
                    "deal": {
                        "currency": "INR",
                        "maximum_price": 25000,
                        "max_stops": 1,
                        "allow_self_transfer": False,
                    },
                    "notifications": {"alert_on_price_drop": {"amount": 1000}},
                }
            ],
        }
    )


def offer(price: str = "18000") -> FareOffer:
    return FareOffer(
        source="mock",
        origin="COK",
        destination="DXB",
        departure_at=datetime(2026, 9, 15, 8, tzinfo=UTC),
        airline="Example Air",
        flight_numbers=["EA1"],
        stops=0,
        layover_minutes=[],
        trip_type="one_way",
        cabin="economy",
        passenger_count=1,
        price=Decimal(price),
        currency="INR",
        booking_url="https://example.com/fare",
        self_transfer=False,
    )


class MockSource:
    name = "mock"

    def __init__(self, prices: list[str] | None = None):
        self.prices = prices or ["18000"]
        self.search_calls: list[SearchRequest] = []
        self.discovery_calls = 0

    def search(self, request: SearchRequest) -> list[FareOffer]:
        self.search_calls.append(request)
        return [offer(self.prices[min(len(self.search_calls) - 1, len(self.prices) - 1)])]

    def discover_dates(self, **kwargs: object) -> list[DateCandidate]:
        self.discovery_calls += 1
        return [
            DateCandidate(departure_date="2026-09-15", price="17000", currency="INR"),
            DateCandidate(departure_date="2026-09-16", price="24000", currency="INR"),
            DateCandidate(departure_date="2026-09-17", price="50000", currency="INR"),
        ]


class MockNotifier:
    def __init__(self, fail: bool = False, name: str = "mock_notifier"):
        self.name = name
        self.fail = fail
        self.sent: list[tuple[Notification, str]] = []

    def send(self, notification: Notification, idempotency_key: str) -> None:
        if self.fail:
            raise RuntimeError("delivery failed")
        self.sent.append((notification, idempotency_key))


def test_exact_cycle_first_alert_and_deduplication(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    source = MockSource()
    notifier = MockNotifier()
    engine = WatchEngine(config(), db, source, [notifier])
    first = engine.run(today=date(2026, 8, 6))
    second = engine.run(today=date(2026, 8, 6))
    assert first["status"] == "success"
    assert first["notifications_sent"] == 1
    assert second["notifications_sent"] == 0
    assert len(notifier.sent) == 1
    observations = db.dashboard_history("sample")
    assert observations[0]["qualifies"] is True
    assert observations[0]["qualification_reason"] == "qualified"
    db.close()


def test_cycle_persists_non_qualifying_reason(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    engine = WatchEngine(config(), db, MockSource(["30000"]), [])
    result = engine.run()
    observation = db.dashboard_history("sample")[0]
    assert result["qualifying_count"] == 0
    assert observation["qualifies"] is False
    assert "price" in observation["qualification_reason"]
    db.close()


def test_price_drop_alert(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    source = MockSource(["18000", "16500"])
    notifier = MockNotifier()
    engine = WatchEngine(config(), db, source, [notifier])
    engine.run()
    result = engine.run()
    assert result["notifications_sent"] == 1
    assert notifier.sent[-1][0].reason == "price_drop"
    db.close()


def test_failed_provider_retries_without_duplicating_success(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    source = MockSource()
    good = MockNotifier()
    failed = MockNotifier(fail=True, name="failed_notifier")
    engine = WatchEngine(config(), db, source, [good, failed])
    engine.run()
    failed.fail = False
    result = engine.run()
    assert len(good.sent) == 1
    assert len(failed.sent) == 1
    assert result["notifications_sent"] == 1
    db.close()


def test_rolling_uses_discovery_then_capped_verification(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    source = MockSource()
    result = WatchEngine(config(rolling=True), db, source, []).run(today=date(2026, 8, 6))
    assert source.discovery_calls == 1
    assert len(source.search_calls) == 2
    assert result["query_budget_usage"] == 3
    db.close()


def test_query_budget_enforced(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    source = MockSource()
    loaded = config(rolling=True, budget=3)
    loaded.scheduler.max_queries_per_cycle = 2
    result = WatchEngine(loaded, db, source, []).run()
    assert result["status"] != "success"
    assert len(source.search_calls) == 1
    db.close()


def test_reappearance_after_cooldown(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    source = MockSource()
    notifier = MockNotifier()
    engine = WatchEngine(config(), db, source, [notifier])
    engine.run()
    original_search = source.search
    source.search = lambda request: []  # type: ignore[method-assign]
    engine.run()
    db.connection.execute(
        "UPDATE notification_attempts SET attempted_at=?",
        ((datetime.now(UTC) - timedelta(hours=25)).isoformat(),),
    )
    source.search = original_search  # type: ignore[method-assign]
    result = engine.run()
    assert result["notifications_sent"] == 1
    assert notifier.sent[-1][0].reason == "reappeared"
    db.close()


def test_health_warning_after_three_failures_and_recovery(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    source = MockSource()
    notifier = MockNotifier()
    engine = WatchEngine(config(), db, source, [notifier])

    def fail(request: SearchRequest) -> list[FareOffer]:
        raise SourceError("upstream unavailable")

    source.search = fail  # type: ignore[method-assign]
    engine.run()
    engine.run()
    engine.run()
    assert [item[0].event for item in notifier.sent] == ["health_unhealthy"]
    source.search = lambda request: []  # type: ignore[method-assign]
    engine.run()
    assert [item[0].event for item in notifier.sent] == [
        "health_unhealthy",
        "health_recovered",
    ]
    db.close()


def test_dry_run_does_not_query_or_create_database_cycle(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    source = MockSource()
    result = WatchEngine(config(), db, source, []).run(dry_run=True, today=date(2026, 8, 6))
    assert result["queries_executed"] == 0
    assert source.search_calls == []
    count = db.connection.execute("SELECT COUNT(*) FROM search_cycles").fetchone()[0]
    assert count == 0
    db.close()


def test_unknown_watch_is_clear(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    with pytest.raises(Exception, match="Unknown watch"):
        WatchEngine(config(), db, MockSource(), []).run({"missing"}, dry_run=True)
    db.close()
