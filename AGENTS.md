# Repository instructions

- Preserve user work and keep changes scoped to AutoFly.
- Use Python 3.12-compatible code, strict typing, Ruff formatting, and offline unit tests.
- Never use `shell=True`, commit secrets, bypass CAPTCHAs, rotate proxies, or automate purchases.
- Fare and notification integrations belong behind their protocols; do not couple adapters to the CLI.
- Live fare checks require explicit `AUTOFly_LIVE_TEST=1` and must remain low-volume.
- Run `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`, and `python -m build` before pushing.
- Document source behavior honestly; null is better than invented itinerary data.

