## Summary

Describe the behavior and why it belongs in AutoFly.

## Validation

- [ ] `ruff format --check .`
- [ ] `ruff check .`
- [ ] `mypy src`
- [ ] `pytest`
- [ ] `python -m build`
- [ ] No live requests, real secrets, credentials, cookies, or personal route history were added.
- [ ] User-facing behavior and limitations are documented.

## Source/notifier checklist (when applicable)

- [ ] Timeouts, bounded output, pacing/backoff, and rate limits are handled.
- [ ] Missing data remains null/unknown rather than invented.
- [ ] Fixtures are sanitized and live tests are explicit opt-in.
- [ ] Upstream version, license, and attribution are documented.

