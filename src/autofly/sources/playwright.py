from __future__ import annotations

import json
import re
from contextlib import suppress
from datetime import date, datetime
from typing import Any
from urllib.parse import quote_plus

from autofly.config import PlaywrightConfig
from autofly.errors import SourceError, SourceOutputError
from autofly.models import FareOffer, SearchRequest
from autofly.sources.base import DateCandidate
from autofly.sources.flight_goat import parse_flights

CAPTCHA_MARKERS = (
    "unusual traffic",
    "verify you are human",
    "not a robot",
    "captcha",
    "automated queries",
)


class CaptchaDetected(SourceError):
    pass


class PlaywrightSource:
    """Conservative browser fallback that only consumes recognized structured responses."""

    name = "playwright"

    def __init__(self, config: PlaywrightConfig):
        self.config = config

    def search(self, request: SearchRequest) -> list[FareOffer]:
        try:
            from playwright.sync_api import (  # type: ignore[import-not-found]
                TimeoutError as PlaywrightTimeout,
            )
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise SourceError(
                "Playwright is enabled but not installed; install autofly[playwright]"
            ) from exc

        self.config.profile_path.mkdir(parents=True, exist_ok=True)
        self.config.diagnostic_path.mkdir(parents=True, exist_ok=True)
        structured: list[dict[str, Any]] = []
        query = (
            f"Flights from {request.origin} to {request.destination} on {request.departure_date}"
        )
        if request.return_date:
            query += f" returning {request.return_date}"
        url = (
            "https://www.google.com/travel/flights?hl="
            f"{quote_plus(self.config.locale)}&curr={request.currency}&q={quote_plus(query)}"
        )
        try:
            with sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    str(self.config.profile_path),
                    headless=self.config.headless,
                    locale=self.config.locale,
                )
                page = context.new_page()

                def inspect_response(response: Any) -> None:
                    content_type = response.headers.get("content-type", "")
                    if "json" not in content_type or "flight" not in response.url.lower():
                        return
                    try:
                        declared_size = int(response.headers.get("content-length", "0") or 0)
                        if declared_size > self.config.max_response_bytes:
                            return
                        body = response.body()
                        if len(body) > self.config.max_response_bytes:
                            return
                        payload = json.loads(body)
                    except Exception:
                        return
                    if isinstance(payload, dict) and isinstance(payload.get("flights"), list):
                        structured.append(payload)

                page.on("response", inspect_response)
                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self.config.timeout_seconds * 1000,
                )
                page.wait_for_load_state("networkidle", timeout=self.config.timeout_seconds * 1000)
                visible = page.locator("body").inner_text(timeout=5000)
                if contains_captcha(visible):
                    self._diagnostic(page, "captcha")
                    raise CaptchaDetected(
                        "Google displayed CAPTCHA/bot verification; "
                        "AutoFly stopped without bypassing it"
                    )
                if not structured:
                    self._diagnostic(page, "unsupported-response")
                    raise SourceOutputError(
                        "Playwright found no recognized structured fare response; "
                        "DOM price scraping "
                        "is intentionally not used because it is too fragile"
                    )
                offers = parse_flights(structured[-1], request)
                context.close()
                return offers
        except CaptchaDetected:
            raise
        except PlaywrightTimeout as exc:
            raise SourceError(
                f"Playwright search timed out after {self.config.timeout_seconds:g}s"
            ) from exc

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
        raise SourceError(
            "Playwright fallback supports exact searches only; "
            "flexible date discovery requires Flight GOAT"
        )

    def _diagnostic(self, page: Any, category: str) -> None:
        safe_category = re.sub(r"[^a-z0-9-]", "-", category.lower())[:40]
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        screenshot = self.config.diagnostic_path / f"playwright-{safe_category}-{stamp}.png"
        metadata = self.config.diagnostic_path / f"playwright-{safe_category}-{stamp}.json"
        with suppress(Exception):
            page.add_style_tag(
                content="header,[aria-label*='Account'],[aria-label*='account']{visibility:hidden!important}"
            )
            main = page.get_by_role("main")
            if main.count():
                main.first.screenshot(path=str(screenshot))
        with suppress(Exception):
            metadata.write_text(
                json.dumps(
                    {
                        "category": safe_category,
                        "timestamp": stamp,
                        "note": "URL, cookies, headers, HTML, and profile were not captured",
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )


def contains_captcha(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in CAPTCHA_MARKERS)
