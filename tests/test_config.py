from datetime import date
from pathlib import Path

import pytest

from autofly.config import AppConfig, ConfigError, load_config
from autofly.dates import strategy_for


def base_config() -> dict:
    return {
        "version": 1,
        "scheduler": {"max_queries_per_cycle": 30},
        "watches": [
            {
                "id": "sample",
                "origins": ["cok", "CCJ"],
                "destinations": ["DXB", "SHJ"],
                "trip": {"type": "one_way", "adults": 1, "cabin": "economy"},
                "dates": {"mode": "rolling", "days_from_now": 1, "days_to": 3},
                "deal": {"currency": "inr", "maximum_price": 25000},
            }
        ],
    }


def test_multiple_watches_and_route_expansion() -> None:
    raw = base_config()
    raw["watches"].append(
        {
            "id": "second",
            "enabled": True,
            "origins": ["LHR"],
            "destinations": ["JFK"],
            "dates": {"mode": "exact", "departure": "2026-12-10"},
            "deal": {"currency": "GBP", "maximum_price": 400},
        }
    )
    config = AppConfig.model_validate(raw)
    assert config.watches[0].route_pairs() == [
        ("COK", "DXB"),
        ("COK", "SHJ"),
        ("CCJ", "DXB"),
        ("CCJ", "SHJ"),
    ]
    assert len(config.watches) == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [("origins", []), ("destinations", []), ("origins", ["COK", "COK"]), ("origins", ["../x"])],
)
def test_invalid_locations(field: str, value: list[str]) -> None:
    raw = base_config()
    raw["watches"][0][field] = value
    with pytest.raises(ValueError):
        AppConfig.model_validate(raw)


def test_origin_cannot_equal_destination() -> None:
    raw = base_config()
    raw["watches"][0]["destinations"] = ["COK"]
    with pytest.raises(ValueError, match="origin equals destination"):
        AppConfig.model_validate(raw)


def test_safety_limit_requires_explicit_override() -> None:
    raw = base_config()
    raw["scheduler"]["max_queries_per_cycle"] = 3
    with pytest.raises(ValueError, match="exceeds"):
        AppConfig.model_validate(raw)


def test_date_modes() -> None:
    raw = base_config()
    rolling = AppConfig.model_validate(raw).watches[0].dates
    assert strategy_for(rolling).dates(date(2026, 8, 6)) == [
        date(2026, 8, 7),
        date(2026, 8, 8),
        date(2026, 8, 9),
    ]
    raw["watches"][0]["dates"] = {
        "mode": "range",
        "departure_start": "2026-09-01",
        "departure_end": "2026-09-02",
    }
    ranged = AppConfig.model_validate(raw).watches[0].dates
    assert strategy_for(ranged).dates(date.today()) == [date(2026, 9, 1), date(2026, 9, 2)]
    raw["watches"][0]["dates"] = {"mode": "exact", "departure": "2026-10-01"}
    exact = AppConfig.model_validate(raw).watches[0].dates
    assert strategy_for(exact).dates(date.today()) == [date(2026, 10, 1)]


def test_exact_round_trip_and_flexible_rejection() -> None:
    raw = base_config()
    raw["watches"][0]["trip"]["type"] = "round_trip"
    raw["watches"][0]["dates"] = {
        "mode": "exact",
        "departure": "2026-12-10",
        "return": "2026-12-20",
    }
    assert AppConfig.model_validate(raw).watches[0].dates.return_date == date(2026, 12, 20)  # type: ignore[union-attr]
    raw["watches"][0]["dates"] = {
        "mode": "range",
        "departure_start": "2026-12-10",
        "departure_end": "2026-12-20",
    }
    with pytest.raises(ValueError, match="flexible round trips"):
        AppConfig.model_validate(raw)


def test_load_error_mentions_watch_id(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "version: 1\nwatches:\n  - id: broken-watch\n    origins: []\n    destinations: [DXB]\n"
        "    dates: {mode: exact, departure: 2026-10-01}\n"
        "    deal: {currency: INR, maximum_price: 100}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="broken-watch"):
        load_config(path)
