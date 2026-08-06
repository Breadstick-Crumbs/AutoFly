from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from autofly.models import FareOffer, SearchRequest

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watch_snapshots (
    id INTEGER PRIMARY KEY, cycle_id TEXT NOT NULL, watch_id TEXT NOT NULL,
    snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS search_cycles (
    id TEXT PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT, status TEXT NOT NULL,
    consecutive_failures INTEGER NOT NULL DEFAULT 0, metrics_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS search_requests (
    id INTEGER PRIMARY KEY, cycle_id TEXT NOT NULL, watch_id TEXT NOT NULL,
    source TEXT NOT NULL, request_json TEXT NOT NULL, status TEXT NOT NULL,
    started_at TEXT NOT NULL, ended_at TEXT, error TEXT,
    FOREIGN KEY(cycle_id) REFERENCES search_cycles(id)
);
CREATE TABLE IF NOT EXISTS itineraries (
    watch_id TEXT NOT NULL, itinerary_id TEXT NOT NULL, source TEXT NOT NULL,
    first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, available INTEGER NOT NULL,
    unavailable_at TEXT, lowest_price TEXT NOT NULL, last_price TEXT NOT NULL,
    currency TEXT NOT NULL, offer_json TEXT NOT NULL,
    PRIMARY KEY(watch_id, itinerary_id)
);
CREATE TABLE IF NOT EXISTS fare_observations (
    id INTEGER PRIMARY KEY, cycle_id TEXT NOT NULL, watch_id TEXT NOT NULL,
    itinerary_id TEXT NOT NULL, observed_at TEXT NOT NULL, price TEXT NOT NULL,
    currency TEXT NOT NULL, offer_json TEXT NOT NULL,
    FOREIGN KEY(cycle_id) REFERENCES search_cycles(id)
);
CREATE INDEX IF NOT EXISTS idx_observations_watch_time
    ON fare_observations(watch_id, observed_at DESC);
CREATE TABLE IF NOT EXISTS notification_attempts (
    id INTEGER PRIMARY KEY, cycle_id TEXT NOT NULL, watch_id TEXT NOT NULL,
    itinerary_id TEXT, provider TEXT NOT NULL, alert_reason TEXT NOT NULL,
    price TEXT, attempted_at TEXT NOT NULL, status TEXT NOT NULL, error TEXT,
    idempotency_key TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_notification_success
    ON notification_attempts(idempotency_key, provider) WHERE status = 'success';
CREATE TABLE IF NOT EXISTS source_failures (
    id INTEGER PRIMARY KEY, cycle_id TEXT NOT NULL, watch_id TEXT,
    source TEXT NOT NULL, category TEXT NOT NULL, message TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rate_limit_events (
    id INTEGER PRIMARY KEY, cycle_id TEXT NOT NULL, source TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS health_state (
    id INTEGER PRIMARY KEY CHECK(id = 1), consecutive_failures INTEGER NOT NULL,
    unhealthy_notified INTEGER NOT NULL, updated_at TEXT NOT NULL
);
INSERT OR IGNORE INTO health_state(id, consecutive_failures, unhealthy_notified, updated_at)
VALUES (1, 0, 0, '1970-01-01T00:00:00+00:00');
"""


@dataclass(frozen=True)
class ObservationState:
    is_new: bool
    was_unavailable: bool
    previous_price: Decimal | None
    previous_lowest: Decimal | None


class Database:
    def __init__(self, path: Path, busy_timeout_seconds: float = 10):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(
            path, timeout=busy_timeout_seconds, isolation_level=None, check_same_thread=False
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(f"PRAGMA busy_timeout={int(busy_timeout_seconds * 1000)}")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    def close(self) -> None:
        self.connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield self.connection
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def migrate(self) -> None:
        with self.transaction() as conn:
            conn.executescript(SCHEMA)
            row = conn.execute("SELECT MAX(version) AS version FROM schema_version").fetchone()
            version = int(row["version"] or 0)
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema {version} is newer than supported {SCHEMA_VERSION}"
                )
            if version < 1:
                conn.execute(
                    "INSERT INTO schema_version(version, applied_at) VALUES (?, ?)",
                    (1, _now()),
                )

    def start_cycle(self) -> str:
        cycle_id = str(uuid.uuid4())
        self.connection.execute(
            "INSERT INTO search_cycles(id, started_at, status) VALUES (?, ?, 'running')",
            (cycle_id, _now()),
        )
        return cycle_id

    def finish_cycle(self, cycle_id: str, status: str, metrics: dict[str, Any]) -> None:
        self.connection.execute(
            "UPDATE search_cycles SET ended_at=?, status=?, metrics_json=? WHERE id=?",
            (_now(), status, json.dumps(metrics, sort_keys=True), cycle_id),
        )

    def snapshot_watch(self, cycle_id: str, watch_id: str, snapshot: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT INTO watch_snapshots(cycle_id, watch_id, snapshot_json, created_at) "
            "VALUES (?, ?, ?, ?)",
            (cycle_id, watch_id, json.dumps(snapshot, sort_keys=True, default=str), _now()),
        )

    def start_request(self, cycle_id: str, request: SearchRequest, source: str) -> int:
        return self.start_request_payload(
            cycle_id, request.watch_id, source, request.model_dump(mode="json")
        )

    def start_request_payload(
        self,
        cycle_id: str,
        watch_id: str,
        source: str,
        payload: dict[str, Any],
    ) -> int:
        cursor = self.connection.execute(
            "INSERT INTO search_requests("
            "cycle_id, watch_id, source, request_json, status, started_at) "
            "VALUES (?, ?, ?, ?, 'running', ?)",
            (cycle_id, watch_id, source, json.dumps(payload, default=str), _now()),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite did not return a search request ID")
        return cursor.lastrowid

    def finish_request(self, request_id: int, status: str, error: str | None = None) -> None:
        self.connection.execute(
            "UPDATE search_requests SET ended_at=?, status=?, error=? WHERE id=?",
            (_now(), status, error, request_id),
        )

    def observe(self, cycle_id: str, watch_id: str, offer: FareOffer) -> ObservationState:
        identity = offer.itinerary_id
        now = offer.observed_at.isoformat()
        payload = json.dumps(offer.safe_dict(), sort_keys=True)
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT available, lowest_price, last_price FROM itineraries "
                "WHERE watch_id=? AND itinerary_id=?",
                (watch_id, identity),
            ).fetchone()
            state = ObservationState(
                is_new=row is None,
                was_unavailable=row is not None and not bool(row["available"]),
                previous_price=Decimal(row["last_price"]) if row else None,
                previous_lowest=Decimal(row["lowest_price"]) if row else None,
            )
            lowest = min(offer.price, state.previous_lowest or offer.price)
            conn.execute(
                "INSERT INTO itineraries(watch_id, itinerary_id, source, first_seen_at, "
                "last_seen_at, available, unavailable_at, lowest_price, last_price, currency, "
                "offer_json) VALUES (?, ?, ?, ?, ?, 1, NULL, ?, ?, ?, ?) "
                "ON CONFLICT(watch_id, itinerary_id) DO UPDATE SET "
                "last_seen_at=excluded.last_seen_at, available=1, unavailable_at=NULL, "
                "lowest_price=excluded.lowest_price, last_price=excluded.last_price, "
                "currency=excluded.currency, offer_json=excluded.offer_json",
                (
                    watch_id,
                    identity,
                    offer.source,
                    now,
                    now,
                    str(lowest),
                    str(offer.price),
                    offer.currency,
                    payload,
                ),
            )
            conn.execute(
                "INSERT INTO fare_observations(cycle_id, watch_id, itinerary_id, observed_at, "
                "price, currency, offer_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (cycle_id, watch_id, identity, now, str(offer.price), offer.currency, payload),
            )
        return state

    def mark_unseen_unavailable(
        self, watch_id: str, observed_ids: set[str], cycle_started_at: datetime
    ) -> int:
        rows = self.connection.execute(
            "SELECT itinerary_id FROM itineraries "
            "WHERE watch_id=? AND available=1 AND last_seen_at < ?",
            (watch_id, cycle_started_at.isoformat()),
        ).fetchall()
        missing = [row["itinerary_id"] for row in rows if row["itinerary_id"] not in observed_ids]
        if not missing:
            return 0
        with self.transaction() as conn:
            conn.executemany(
                "UPDATE itineraries SET available=0, unavailable_at=? "
                "WHERE watch_id=? AND itinerary_id=?",
                [(_now(), watch_id, item) for item in missing],
            )
        return len(missing)

    def last_successful_alert(
        self, watch_id: str, itinerary_id: str, provider: str | None = None
    ) -> tuple[Decimal, datetime] | None:
        if provider:
            row = self.connection.execute(
                "SELECT price, attempted_at FROM notification_attempts "
                "WHERE watch_id=? AND itinerary_id=? AND status='success' AND provider=? "
                "ORDER BY attempted_at DESC LIMIT 1",
                (watch_id, itinerary_id, provider),
            ).fetchone()
        else:
            row = self.connection.execute(
                "SELECT price, attempted_at FROM notification_attempts "
                "WHERE watch_id=? AND itinerary_id=? AND status='success' "
                "ORDER BY attempted_at DESC LIMIT 1",
                (watch_id, itinerary_id),
            ).fetchone()
        if row is None or row["price"] is None:
            return None
        return Decimal(row["price"]), datetime.fromisoformat(row["attempted_at"])

    def record_notification(
        self,
        *,
        cycle_id: str,
        watch_id: str,
        itinerary_id: str | None,
        provider: str,
        reason: str,
        price: Decimal | None,
        status: str,
        idempotency_key: str,
        error: str | None = None,
    ) -> None:
        self.connection.execute(
            "INSERT INTO notification_attempts(cycle_id, watch_id, itinerary_id, provider, "
            "alert_reason, price, attempted_at, status, error, idempotency_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cycle_id,
                watch_id,
                itinerary_id,
                provider,
                reason,
                str(price) if price is not None else None,
                _now(),
                status,
                error,
                idempotency_key,
            ),
        )

    def record_failure(
        self, cycle_id: str, source: str, category: str, message: str, watch_id: str | None
    ) -> None:
        self.connection.execute(
            "INSERT INTO source_failures("
            "cycle_id, watch_id, source, category, message, occurred_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cycle_id, watch_id, source, category, message[:1000], _now()),
        )
        if category == "rate_limit":
            self.connection.execute(
                "INSERT INTO rate_limit_events(cycle_id, source, occurred_at) VALUES (?, ?, ?)",
                (cycle_id, source, _now()),
            )

    def update_health(self, successful: bool) -> tuple[bool, bool]:
        """Return (send_unhealthy, send_recovery)."""
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT consecutive_failures, unhealthy_notified FROM health_state WHERE id=1"
            ).fetchone()
            failures = 0 if successful else int(row["consecutive_failures"]) + 1
            unhealthy = bool(row["unhealthy_notified"])
            send_unhealthy = failures >= 3 and not unhealthy
            send_recovery = successful and unhealthy
            conn.execute(
                "UPDATE health_state SET consecutive_failures=?, "
                "unhealthy_notified=?, updated_at=? "
                "WHERE id=1",
                (failures, int((unhealthy or send_unhealthy) and not send_recovery), _now()),
            )
        return send_unhealthy, send_recovery

    def history(self, watch_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        query = "SELECT * FROM fare_observations"
        params: list[Any] = []
        if watch_id:
            query += " WHERE watch_id=?"
            params.append(watch_id)
        query += " ORDER BY observed_at DESC LIMIT ?"
        params.append(limit)
        return [dict(row) for row in self.connection.execute(query, params).fetchall()]

    def recent_cycles(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT id, started_at, ended_at, status, metrics_json FROM search_cycles "
            "ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            result.append(
                {
                    "id": row["id"],
                    "started_at": row["started_at"],
                    "ended_at": row["ended_at"],
                    "status": row["status"],
                    "metrics": json.loads(row["metrics_json"]),
                }
            )
        return result

    def dashboard_history(
        self, watch_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        rows = self.history(watch_id, limit)
        result: list[dict[str, Any]] = []
        for row in rows:
            offer = json.loads(row.pop("offer_json"))
            result.append(
                {
                    **row,
                    "source": offer.get("source"),
                    "origin": offer.get("origin"),
                    "destination": offer.get("destination"),
                    "departure_at": offer.get("departure_at"),
                    "return_date": offer.get("return_date"),
                    "airline": offer.get("airline"),
                    "stops": offer.get("stops"),
                    "booking_url": offer.get("booking_url"),
                }
            )
        return result

    def dashboard_summary(self) -> dict[str, Any]:
        itinerary = self.connection.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN available=1 THEN 1 ELSE 0 END) AS available "
            "FROM itineraries"
        ).fetchone()
        notifications = self.connection.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS successful "
            "FROM notification_attempts"
        ).fetchone()
        health = self.connection.execute(
            "SELECT consecutive_failures, unhealthy_notified, updated_at "
            "FROM health_state WHERE id=1"
        ).fetchone()
        return {
            "itineraries": int(itinerary["total"] or 0),
            "available_itineraries": int(itinerary["available"] or 0),
            "notification_attempts": int(notifications["total"] or 0),
            "successful_notifications": int(notifications["successful"] or 0),
            "consecutive_failures": int(health["consecutive_failures"]),
            "unhealthy_notified": bool(health["unhealthy_notified"]),
            "health_updated_at": health["updated_at"],
        }


def cooldown_elapsed(last_alert_at: datetime, cooldown_hours: float, now: datetime) -> bool:
    return now - last_alert_at >= timedelta(hours=cooldown_hours)


def _now() -> str:
    return datetime.now(UTC).isoformat()
