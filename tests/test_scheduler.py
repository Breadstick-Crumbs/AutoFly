import pytest

from autofly.scheduler import next_delay_seconds


def test_next_delay_includes_bounded_jitter() -> None:
    assert next_delay_seconds(6, 10, random_value=0) == 21600
    assert next_delay_seconds(6, 10, random_value=1) == 22200
    assert next_delay_seconds(6, 10, random_value=0.5) == 21900


def test_next_delay_rejects_invalid_random_value() -> None:
    with pytest.raises(ValueError):
        next_delay_seconds(6, 10, random_value=1.1)
