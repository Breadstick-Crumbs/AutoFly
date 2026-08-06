import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Thread

import pytest

from autofly.config import AppConfig
from autofly.database import SCHEMA_VERSION, Database, cooldown_elapsed
from autofly.deals import decide_alert, evaluate_offer
from autofly.errors import LockUnavailable
from autofly.locking import ProcessLock
from autofly.models import FareOffer


def watch():
    return AppConfig.model_validate(
        {
            "version": 1,
            "watches": [
                {
                    "id": "deal",
                    "origins": ["COK"],
                    "destinations": ["DXB"],
                    "dates": {"mode": "exact", "departure": "2026-09-15"},
                    "deal": {
                        "currency": "INR",
                        "maximum_price": 25000,
                        "max_stops": 1,
                        "max_layover_hours": 8,
                        "allow_self_transfer": False,
                    },
                    "notifications": {
                        "cooldown_hours": 24,
                        "alert_on_price_drop": {"amount": 1000, "percentage": 5},
                    },
                }
            ],
        }
    ).watches[0]


def offer(**changes: object) -> FareOffer:
    data: dict[str, object] = {
        "source": "flight_goat",
        "origin": "COK",
        "destination": "DXB",
        "departure_at": "2026-09-15T08:55:00+05:30",
        "airline": "Air India Express",
        "flight_numbers": ["IX425"],
        "stops": 0,
        "layover_minutes": [],
        "duration_minutes": 250,
        "trip_type": "one_way",
        "cabin": "economy",
        "passenger_count": 1,
        "price": "18740",
        "currency": "INR",
        "booking_url": "https://example.com/search",
        "self_transfer": False,
        "observed_at": "2026-08-06T12:00:00+00:00",
    }
    data.update(changes)
    return FareOffer.model_validate(data)


def test_itinerary_identity_ignores_price() -> None:
    assert offer(price="18000").itinerary_id == offer(price="19000").itinerary_id
    assert offer(flight_numbers=["IX999"]).itinerary_id != offer().itinerary_id


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"price": "25000"}, "price"),
        ({"currency": "USD"}, "currency"),
        ({"stops": 2}, "stop"),
        ({"layover_minutes": [600]}, "layover"),
        ({"self_transfer": True}, "self-transfer"),
        ({"self_transfer": None}, "self-transfer"),
    ],
)
def test_deal_filters(changes: dict[str, object], reason: str) -> None:
    result = evaluate_offer(offer(**changes), watch())
    assert not result.qualifies
    assert reason in result.reason


def test_qualifying_deal() -> None:
    assert evaluate_offer(offer(), watch()).qualifies


def test_first_price_drop_reappearance_and_unchanged_alerts() -> None:
    target = watch()
    assert (
        decide_alert(
            current_price=Decimal("18000"),
            last_alert_price=None,
            reappeared_after_cooldown=False,
            watch=target,
        ).reason
        == "first_qualified"
    )
    assert (
        decide_alert(
            current_price=Decimal("16900"),
            last_alert_price=Decimal("18000"),
            reappeared_after_cooldown=False,
            watch=target,
        ).reason
        == "price_drop"
    )
    assert (
        decide_alert(
            current_price=Decimal("18000"),
            last_alert_price=Decimal("18000"),
            reappeared_after_cooldown=True,
            watch=target,
        ).reason
        == "reappeared"
    )
    assert not decide_alert(
        current_price=Decimal("17950"),
        last_alert_price=Decimal("18000"),
        reappeared_after_cooldown=False,
        watch=target,
    ).send


def test_database_migration_observation_and_notification(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    version = db.connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    assert version == SCHEMA_VERSION
    cycle = db.start_cycle()
    state = db.observe(
        cycle,
        "deal",
        offer(),
        qualifies=True,
        qualification_reason="qualified",
    )
    assert state.is_new
    lowered = offer(price="17000", observed_at="2026-08-07T12:00:00+00:00")
    state2 = db.observe(cycle, "deal", lowered)
    assert state2.previous_price == Decimal("18740")
    db.record_notification(
        cycle_id=cycle,
        watch_id="deal",
        itinerary_id=lowered.itinerary_id,
        provider="telegram",
        reason="price_drop",
        price=lowered.price,
        status="success",
        idempotency_key="key-1",
    )
    assert db.last_successful_alert("deal", lowered.itinerary_id)[0] == Decimal("17000")  # type: ignore[index]
    history = db.dashboard_history("deal", qualifying=True)
    assert history[0]["qualifies"] is True
    assert history[0]["qualification_reason"] == "qualified"
    assert db.dashboard_trend("deal", "COK", "DXB")[0]["price"] == 17000.0
    assert db.dashboard_notifications()[0]["status"] == "success"
    with pytest.raises(sqlite3.IntegrityError):
        db.record_notification(
            cycle_id=cycle,
            watch_id="deal",
            itinerary_id=lowered.itinerary_id,
            provider="telegram",
            reason="price_drop",
            price=lowered.price,
            status="success",
            idempotency_key="key-1",
        )
    db.close()


def test_database_migrates_v1_observations_for_rule_explanations(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
        INSERT INTO schema_version(version, applied_at) VALUES (1, '2026-08-06');
        CREATE TABLE fare_observations (
            id INTEGER PRIMARY KEY, cycle_id TEXT NOT NULL, watch_id TEXT NOT NULL,
            itinerary_id TEXT NOT NULL, observed_at TEXT NOT NULL, price TEXT NOT NULL,
            currency TEXT NOT NULL, offer_json TEXT NOT NULL
        );
        """
    )
    connection.close()

    migrated = Database(path)
    columns = {
        row["name"]
        for row in migrated.connection.execute("PRAGMA table_info(fare_observations)").fetchall()
    }
    assert {"qualifies", "qualification_reason"} <= columns
    assert migrated.connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 2
    migrated.close()


def test_concurrent_database_access(tmp_path: Path) -> None:
    path = tmp_path / "concurrent.sqlite"
    seed = Database(path)
    cycle = seed.start_cycle()
    seed.close()
    errors: list[Exception] = []

    def writer(index: int) -> None:
        try:
            db = Database(path)
            item = offer(
                flight_numbers=[f"IX{index}"],
                observed_at=(
                    datetime(2026, 8, 6, tzinfo=UTC) + timedelta(seconds=index)
                ).isoformat(),
            )
            db.observe(cycle, "deal", item)
            db.close()
        except Exception as exc:  # pragma: no cover - assertion captures it
            errors.append(exc)

    threads = [Thread(target=writer, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    check = Database(path)
    assert len(check.history()) == 8
    check.close()


def test_process_lock(tmp_path: Path) -> None:
    first = ProcessLock(tmp_path / "autofly.lock")
    second = ProcessLock(tmp_path / "autofly.lock")
    first.acquire()
    with pytest.raises(LockUnavailable):
        second.acquire()
    first.release()
    second.acquire()
    second.release()


def test_cooldown() -> None:
    now = datetime(2026, 8, 6, tzinfo=UTC)
    assert cooldown_elapsed(now - timedelta(hours=24), 24, now)
    assert not cooldown_elapsed(now - timedelta(hours=23), 24, now)
