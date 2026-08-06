from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from typing import Any

from autofly.config import FlightGoatConfig
from autofly.errors import SourceError, SourceOutputError, SourceRateLimited
from autofly.models import FareOffer, SearchRequest
from autofly.sources.base import DateCandidate

RunResult = tuple[int, bytes, bytes]
Runner = Callable[[list[str], float, int], RunResult]


class FlightGoatSource:
    name = "flight_goat"

    def __init__(
        self,
        config: FlightGoatConfig,
        *,
        runner: Runner | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.config = config
        self._runner = runner or run_limited
        self._sleep = sleeper
        self._clock = clock
        self._last_request_at: float | None = None

    def version(self) -> str:
        payload = self._invoke(["--version"], pace=False, retries=0, parse_json=False)
        return str(payload).strip()

    def doctor(self) -> dict[str, Any]:
        payload = self._invoke(["doctor", "--agent", "--no-learn"], pace=False, retries=0)
        if not isinstance(payload, dict):
            raise SourceOutputError("Flight GOAT doctor returned a non-object")
        return payload

    def search(self, request: SearchRequest) -> list[FareOffer]:
        args = [
            "flights",
            request.origin,
            request.destination,
            request.departure_date.isoformat(),
            "--currency",
            request.currency,
            "--class",
            request.cabin,
            "--passengers",
            str(request.adults),
            "--agent",
            "--no-learn",
        ]
        if request.return_date:
            args.extend(["--return", request.return_date.isoformat()])
        if request.max_stops is not None:
            args.extend(["--stops", _stops_flag(request.max_stops)])
        if request.max_layover_minutes is not None:
            args.extend(["--max-layover", str(request.max_layover_minutes)])
        payload = self._invoke(args)
        return parse_flights(payload, request)

    def discover_dates(
        self,
        *,
        origin: str,
        destination: str,
        start: date,
        end: date,
        currency: str,
        cabin: str,
        max_stops: int | None,
    ) -> list[DateCandidate]:
        args = [
            "dates",
            origin,
            destination,
            "--from",
            start.isoformat(),
            "--to",
            end.isoformat(),
            "--currency",
            currency,
            "--class",
            cabin,
            "--sort",
            "--agent",
            "--no-learn",
        ]
        if max_stops is not None:
            if max_stops > 1:
                args.extend(["--stops", "any"])
            else:
                args.extend(["--stops", _stops_flag(max_stops)])
        payload = self._invoke(args)
        return parse_dates(payload)

    def _invoke(
        self,
        args: list[str],
        *,
        pace: bool = True,
        retries: int | None = None,
        parse_json: bool = True,
    ) -> Any:
        if pace and self._last_request_at is not None:
            remaining = self.config.pace_seconds - (self._clock() - self._last_request_at)
            if remaining > 0:
                self._sleep(remaining)
        attempts = self.config.max_retries if retries is None else retries
        command = [self.config.command, *args]
        for attempt in range(attempts + 1):
            try:
                code, stdout, stderr = self._runner(
                    command, self.config.timeout_seconds, self.config.max_output_bytes
                )
            except subprocess.TimeoutExpired as exc:
                if attempt >= attempts:
                    raise SourceError(
                        f"Flight GOAT timed out after {self.config.timeout_seconds:g}s"
                    ) from exc
                self._sleep(2**attempt)
                continue
            finally:
                if pace:
                    self._last_request_at = self._clock()
            safe_error = _safe_error(stderr)
            if code == 7 or b"429" in stderr or b"rate limit" in stderr.lower():
                raise SourceRateLimited("Flight GOAT reported persistent HTTP 429 rate limiting")
            if code != 0:
                if attempt < attempts and code == 5:
                    self._sleep(2**attempt)
                    continue
                raise SourceError(f"Flight GOAT exited with code {code}: {safe_error}")
            if not parse_json:
                return stdout.decode("utf-8", errors="replace")
            try:
                return json.loads(stdout)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise SourceOutputError("Flight GOAT returned malformed JSON") from exc
        raise AssertionError("unreachable")


def run_limited(command: list[str], timeout: float, max_bytes: int) -> RunResult:
    """Run without a shell and read no more than max_bytes from either output stream."""
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(  # noqa: S603 - command is an explicit trusted executable + flags
            command,
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            shell=False,
        )
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read(max_bytes + 1)
        stderr = stderr_file.read(max_bytes + 1)
        if len(stdout) > max_bytes or len(stderr) > max_bytes:
            raise SourceOutputError(f"Flight GOAT output exceeded {max_bytes} bytes")
        return code, stdout, stderr


def parse_dates(payload: Any) -> list[DateCandidate]:
    if not isinstance(payload, dict) or not isinstance(payload.get("dates"), list):
        raise SourceOutputError("Flight GOAT dates output has no dates array")
    try:
        return [DateCandidate.model_validate(item) for item in payload["dates"]]
    except (ValueError, TypeError) as exc:
        raise SourceOutputError("Flight GOAT dates output failed validation") from exc


def parse_flights(payload: Any, request: SearchRequest) -> list[FareOffer]:
    if not isinstance(payload, dict) or not isinstance(payload.get("flights"), list):
        raise SourceOutputError("Flight GOAT flights output has no flights array")
    result: list[FareOffer] = []
    for index, raw in enumerate(payload["flights"]):
        try:
            valid_legs = isinstance(raw, dict) and isinstance(raw.get("legs"), list)
            if not valid_legs or not raw["legs"]:
                raise ValueError("missing legs")
            legs = raw["legs"]
            first, last = legs[0], legs[-1]
            departure_at = datetime.fromisoformat(first["departure_time"])
            flight_numbers = [
                f"{leg.get('airline', {}).get('code', '')}{leg['flight_number']}" for leg in legs
            ]
            airlines = [leg.get("airline", {}).get("name") for leg in legs]
            airline = ", ".join(dict.fromkeys(item for item in airlines if item)) or None
            layovers = []
            for previous, following in pairwise(legs):
                arrival = datetime.fromisoformat(previous["arrival_time"])
                departure = datetime.fromisoformat(following["departure_time"])
                layovers.append(max(0, int((departure - arrival).total_seconds() / 60)))
            urls = raw.get("booking_urls") or {}
            booking_url = urls.get("primary") or urls.get("google_url")
            if not booking_url:
                raise ValueError("missing booking URL")
            stops = int(raw["stops"])
            result.append(
                FareOffer(
                    source="flight_goat",
                    origin=first["departure_airport"]["code"],
                    destination=last["arrival_airport"]["code"],
                    departure_at=departure_at,
                    return_date=request.return_date,
                    airline=airline,
                    flight_numbers=flight_numbers,
                    stops=stops,
                    layover_minutes=layovers,
                    duration_minutes=int(raw["duration"]) if raw.get("duration") else None,
                    trip_type=request.trip_type,
                    cabin=request.cabin,
                    passenger_count=request.adults,
                    price=Decimal(str(raw["price"])),
                    currency=str(raw["currency"]),
                    booking_url=booking_url,
                    self_transfer=False if stops == 0 else None,
                    baggage=None,
                    raw_reference=f"flight-index:{index}",
                )
            )
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise SourceOutputError(f"Flight GOAT flight {index} failed validation") from exc
    return result


def _stops_flag(max_stops: int) -> str:
    return {0: "non_stop", 1: "one_stop"}.get(max_stops, "two_plus_stops")


def _safe_error(stderr: bytes) -> str:
    text = stderr[:1000].decode("utf-8", errors="replace")
    text = re.sub(r"(?i)bearer\s+\S+", "Bearer [REDACTED]", text)
    text = re.sub(
        r"(?i)(token|api[_-]?key|cookie|authorization)\s*[:=]\s*\S+",
        r"\1=[REDACTED]",
        text,
    )
    text = re.sub(r"(https?://[^\s?]+)\?\S+", r"\1?[REDACTED]", text)
    cleaned = " ".join(line for line in text.splitlines() if "authorization" not in line.lower())
    return cleaned.replace("\r", " ").replace("\n", " ") or "no diagnostic output"
