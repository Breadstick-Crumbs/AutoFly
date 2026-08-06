from datetime import date
from pathlib import Path

import pytest

from autofly.config import PlaywrightConfig
from autofly.errors import SourceError
from autofly.models import SearchRequest
from autofly.sources.playwright import PlaywrightSource, contains_captcha


@pytest.mark.parametrize(
    "text",
    [
        "Our systems have detected unusual traffic",
        "Please verify you are human",
        "Complete the CAPTCHA",
        "I am not a robot",
    ],
)
def test_captcha_detection(text: str) -> None:
    assert contains_captcha(text)


def test_normal_page_is_not_captcha() -> None:
    assert not contains_captcha("Choose a departing flight")


def test_flexible_discovery_is_honestly_unsupported(tmp_path: Path) -> None:
    source = PlaywrightSource(PlaywrightConfig(profile_path=tmp_path / "profile"))
    with pytest.raises(SourceError, match="exact searches only"):
        source.discover_dates(
            origin="COK",
            destination="DXB",
            start=date(2026, 9, 1),
            end=date(2026, 9, 2),
            currency="INR",
            cabin="economy",
            max_stops=1,
        )


def test_request_model_prevents_argument_injection() -> None:
    with pytest.raises(ValueError):
        SearchRequest(
            watch_id="x",
            origin="COK --evil",
            destination="DXB",
            departure_date="2026-09-01",
            currency="INR",
        )
