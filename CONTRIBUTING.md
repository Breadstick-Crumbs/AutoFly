# Contributing to AutoFly

Thank you for helping make self-hosted fare monitoring safer and more useful.

1. Discuss large behavior or schema changes in an issue first.
2. Create a focused branch, add tests that make no external requests, and update documentation.
3. Install with `pip install -e ".[dev]"` and run:

   ```bash
   ruff check .
   ruff format --check .
   mypy src
   pytest
   python -m build
   ```

4. Keep live tests behind `AUTOFly_LIVE_TEST=1`; never put credentials in fixtures or CI.
5. Use concise conventional commits and explain user-visible behavior in the pull request.

Contributions are accepted under Apache-2.0. By submitting a contribution you represent that you have the right to license it under those terms. Follow the [Code of Conduct](CODE_OF_CONDUCT.md), report vulnerabilities through [SECURITY.md](SECURITY.md), and use the dedicated guides for [fare sources](docs/adding-fare-source.md) and [notifiers](docs/adding-notifier.md).

