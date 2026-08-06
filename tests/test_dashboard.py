from __future__ import annotations

import sys
import time
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from autofly.cli import app
from autofly.config import WatchConfig
from autofly.dashboard.app import create_app
from autofly.dashboard.config_store import ConfigStore
from autofly.dashboard.service import DashboardService
from autofly.errors import ConfigError
from autofly.sources.base import DateCandidate

runner = CliRunner()


def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    raw = {
        "version": 1,
        "database": {"path": str(tmp_path / "autofly.db")},
        "scheduler": {
            "interval_hours": 6,
            "jitter_minutes": 10,
            "timezone": "UTC",
            "max_queries_per_cycle": 50,
            "lock_path": str(tmp_path / "autofly.lock"),
        },
        "sources": {"flight_goat": {"enabled": True, "command": sys.executable}},
        "notifications": {"telegram": {"enabled": False}, "webhook": {"enabled": False}},
        "watches": [watch_payload()],
    }
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


def watch_payload(identifier: str = "cok-dxb") -> dict[str, object]:
    return {
        "id": identifier,
        "enabled": True,
        "origins": ["COK"],
        "destinations": ["DXB"],
        "trip": {"type": "one_way", "adults": 1, "cabin": "economy"},
        "dates": {"mode": "range", "departure_start": "2026-08-10", "departure_end": "2026-08-20"},
        "deal": {
            "currency": "INR",
            "maximum_price": 25000,
            "max_stops": 1,
            "allow_self_transfer": False,
        },
        "notifications": {
            "cooldown_hours": 24,
            "alert_on_price_drop": {"amount": 1000, "percentage": 5},
        },
    }


def test_dashboard_state_is_sanitized_and_sets_security_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = config_file(tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "never-return-this-value")
    client = TestClient(create_app(path))

    page = client.get("/")
    response = client.get("/api/state")

    assert page.status_code == 200
    assert "Flight watch control room" in page.text
    assert response.status_code == 200
    assert response.json()["watches"][0]["id"] == "cok-dxb"
    assert "never-return-this-value" not in response.text
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_dashboard_basic_authentication(tmp_path: Path) -> None:
    client = TestClient(
        create_app(config_file(tmp_path), password="correct-horse")  # noqa: S106 - fixture
    )

    assert client.get("/api/state").status_code == 401
    assert client.get("/api/state", auth=("autofly", "wrong")).status_code == 401
    assert client.get("/api/state", auth=("autofly", "correct-horse")).status_code == 200


def test_watch_update_requires_guard_and_creates_backup(tmp_path: Path) -> None:
    path = config_file(tmp_path)
    client = TestClient(create_app(path))
    payload = watch_payload()
    payload["deal"]["maximum_price"] = 30000  # type: ignore[index]

    unguarded = client.put("/api/watches/cok-dxb", json={"watch": payload})
    guarded = client.put(
        "/api/watches/cok-dxb",
        json={"watch": payload},
        headers={"X-AutoFly-Request": "dashboard"},
    )

    assert unguarded.status_code == 403
    assert guarded.status_code == 200
    assert (
        yaml.safe_load(path.read_text(encoding="utf-8"))["watches"][0]["deal"]["maximum_price"]
        == 30000
    )
    assert path.with_suffix(".yaml.bak").exists()


def test_cross_origin_mutation_is_rejected(tmp_path: Path) -> None:
    client = TestClient(create_app(config_file(tmp_path)))
    response = client.post(
        "/api/watches/cok-dxb/enabled",
        json={"enabled": False},
        headers={"X-AutoFly-Request": "dashboard", "Origin": "https://attacker.invalid"},
    )
    assert response.status_code == 403


def test_invalid_whole_config_does_not_replace_user_file(tmp_path: Path) -> None:
    path = config_file(tmp_path)
    original = path.read_text(encoding="utf-8")
    oversized = WatchConfig.model_validate(
        {
            **watch_payload("oversized"),
            "origins": [f"A{letter}A" for letter in "BCDEFGHIJ"],
            "destinations": [f"B{letter}B" for letter in "CDEFGHIJK"],
        }
    )

    with pytest.raises(ConfigError, match="worst-case query count"):
        ConfigStore(path).save_watch(oversized)

    assert path.read_text(encoding="utf-8") == original


def test_non_loopback_web_requires_explicit_security(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AUTOFLY_WEB_PASSWORD", raising=False)
    result = runner.invoke(
        app,
        [
            "web",
            "--host",
            "0.0.0.0",  # noqa: S104 - verifies unsafe binding is rejected
            "--config",
            str(config_file(tmp_path)),
        ],
    )
    assert result.exit_code == 2
    assert "requires --allow-remote" in result.output


def test_non_loopback_web_rejects_short_password(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AUTOFLY_WEB_PASSWORD", "too-short")
    result = runner.invoke(
        app,
        [
            "web",
            "--host",
            "0.0.0.0",  # noqa: S104 - verifies unsafe binding is rejected
            "--allow-remote",
            "--config",
            str(config_file(tmp_path)),
        ],
    )
    assert result.exit_code == 2
    assert "at least 16 characters" in result.output


def test_assets_are_served_without_external_dependencies(tmp_path: Path) -> None:
    client = TestClient(create_app(config_file(tmp_path)))
    script = client.get("/assets/app.js")
    styles = client.get("/assets/styles.css")
    assert script.status_code == 200
    assert styles.status_code == 200
    assert "https://" not in script.text
    assert "@import" not in styles.text


class EmptyFareSource:
    name = "test_source"

    def discover_dates(self, **_: Any) -> list[DateCandidate]:
        return [
            DateCandidate(departure_date=date(2026, 8, 10), price=Decimal("20000"), currency="INR")
        ]

    def search(self, request: Any) -> list[Any]:
        return []


def test_manual_check_job_runs_through_process_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = config_file(tmp_path)
    monkeypatch.setattr("autofly.dashboard.service._source", lambda config: EmptyFareSource())
    dashboard = DashboardService(path)

    job = dashboard.start_check("cok-dxb")
    deadline = time.monotonic() + 3
    while dashboard.job(job.id).status in {"queued", "running"} and time.monotonic() < deadline:
        time.sleep(0.01)

    completed = dashboard.job(job.id)
    assert completed.status == "completed"
    assert completed.result is not None
    assert completed.result["status"] == "success"
    assert completed.result["query_budget_usage"] == 2
    assert dashboard.state()["cycles"][0]["status"] == "success"
