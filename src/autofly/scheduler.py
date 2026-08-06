from __future__ import annotations

import random


def next_delay_seconds(
    interval_hours: float,
    jitter_minutes: int,
    *,
    random_value: float | None = None,
) -> float:
    """Return the bounded delay before the next scheduled cycle."""
    value = random.random() if random_value is None else random_value  # noqa: S311 - scheduling only
    if not 0 <= value <= 1:
        raise ValueError("random_value must be between 0 and 1")
    return interval_hours * 3600 + value * jitter_minutes * 60
