import json
import subprocess
from datetime import date
from pathlib import Path

import pytest

from autofly.config import FlightGoatConfig
from autofly.errors import SourceError, SourceOutputError, SourceRateLimited
from autofly.models import SearchRequest
from autofly.sources.flight_goat import FlightGoatSource, parse_dates, parse_flights, run_limited

FIXTURES = Path(__file__).parent / "fixtures"


def request() -> SearchRequest:
    return SearchRequest(
        watch_id="sample",
        origin="COK",
        destination="DXB",
        departure_date="2026-09-15",
        trip_type="one_way",
        cabin="economy",
        adults=1,
        currency="INR",
        max_stops=1,
        max_layover_minutes=480,
    )


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_parse_live_sanitized_flights_and_price() -> None:
    offers = parse_flights(json.loads(fixture("flight_goat_flights.json")), request())
    assert len(offers) == 2
    assert str(offers[0].price) == "22541"
    assert offers[0].flight_numbers == ["IX425"]
    assert offers[0].self_transfer is False
    assert offers[1].layover_minutes == [560]
    assert offers[1].self_transfer is None
    assert offers[0].baggage is None


def test_parse_dates_and_currency() -> None:
    dates = parse_dates(json.loads(fixture("flight_goat_dates.json")))
    assert dates[0].departure_date == date(2026, 9, 17)
    assert dates[0].currency == "INR"


@pytest.mark.parametrize("payload", [{}, {"flights": "bad"}, {"flights": [{}]}])
def test_malformed_flight_goat_output(payload: object) -> None:
    with pytest.raises(SourceOutputError):
        parse_flights(payload, request())


def test_isolated_invalid_flight_does_not_discard_valid_offers() -> None:
    payload = json.loads(fixture("flight_goat_flights.json"))
    payload["flights"].append({"price": 0})

    offers = parse_flights(payload, request())

    assert len(offers) == 2


def test_safe_argument_array() -> None:
    seen: list[list[str]] = []

    def runner(args: list[str], timeout: float, limit: int):
        seen.append(args)
        return 0, fixture("flight_goat_flights.json"), b""

    source = FlightGoatSource(FlightGoatConfig(command="safe-binary"), runner=runner)
    source.search(request())
    assert seen[0][0] == "safe-binary"
    assert seen[0][1:5] == ["flights", "COK", "DXB", "2026-09-15"]
    assert "--max-layover" in seen[0]


def test_subprocess_timeout_retries_then_fails() -> None:
    calls = 0

    def runner(args: list[str], timeout: float, limit: int):
        nonlocal calls
        calls += 1
        raise subprocess.TimeoutExpired(args, timeout)

    source = FlightGoatSource(
        FlightGoatConfig(max_retries=1), runner=runner, sleeper=lambda _: None
    )
    with pytest.raises(SourceError, match="timed out"):
        source.search(request())
    assert calls == 2


def test_missing_executable_is_reported_cleanly() -> None:
    def runner(args: list[str], timeout: float, limit: int):
        raise FileNotFoundError(args[0])

    source = FlightGoatSource(
        FlightGoatConfig(command="missing-flight-goat", max_retries=0), runner=runner
    )
    with pytest.raises(SourceError, match="Cannot start Flight GOAT executable"):
        source.search(request())


@pytest.mark.parametrize(("code", "stderr"), [(7, b"rate limited"), (5, b"HTTP 429 from upstream")])
def test_http_429_stops_without_retry(code: int, stderr: bytes) -> None:
    calls = 0

    def runner(args: list[str], timeout: float, limit: int):
        nonlocal calls
        calls += 1
        return code, b"", stderr

    source = FlightGoatSource(FlightGoatConfig(max_retries=2), runner=runner)
    with pytest.raises(SourceRateLimited):
        source.search(request())
    assert calls == 1


def test_malformed_json() -> None:
    source = FlightGoatSource(
        FlightGoatConfig(), runner=lambda args, timeout, limit: (0, b"not json", b"")
    )
    with pytest.raises(SourceOutputError, match="malformed JSON"):
        source.search(request())


def test_output_size_limit() -> None:
    import sys

    with pytest.raises(SourceOutputError, match="exceeded"):
        run_limited([sys.executable, "-c", "print('x' * 1000)"], 5, 100)


def test_exact_round_trip_arguments_and_normalization() -> None:
    seen: list[str] = []

    def runner(args: list[str], timeout: float, limit: int):
        seen.extend(args)
        return 0, fixture("flight_goat_flights.json"), b""

    round_trip = request().model_copy(
        update={"trip_type": "round_trip", "return_date": date(2026, 9, 20)}
    )
    offers = FlightGoatSource(FlightGoatConfig(), runner=runner).search(round_trip)
    assert seen[seen.index("--return") + 1] == "2026-09-20"
    assert offers[0].trip_type == "round_trip"
    assert offers[0].return_date == date(2026, 9, 20)


@pytest.mark.live
def test_live_search_is_explicitly_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    if os.environ.get("AUTOFly_LIVE_TEST") != "1":  # noqa: SIM112 - documented public name
        pytest.skip("set AUTOFly_LIVE_TEST=1 to run a low-volume source check")
    command = os.environ.get("AUTOFLY_FLIGHT_GOAT_COMMAND", "flight-goat-pp-cli")
    source = FlightGoatSource(FlightGoatConfig(command=command, max_retries=0))
    assert source.search(request())
