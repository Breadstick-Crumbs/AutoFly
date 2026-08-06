# Adding a fare source

Implement `FareSource` in `src/autofly/sources/` and return validated `FareOffer` objects. `search` handles one exact `SearchRequest`; `discover_dates` returns low-cost date candidates or raises a clear unsupported error. Do not modify scheduling, deals, persistence, or notifications for source-specific fields.

Rules:

- Use explicit timeouts, bounded bodies/output, low-volume sequential requests, and transient backoff.
- Never build shell command strings, log secrets, bypass access controls/CAPTCHAs, rotate proxies, or automate purchasing.
- Preserve currency; do not invent conversion, baggage, self-transfer, timestamps, or booking links.
- Use null/unknown for absent fields. Offers without required route/date/price/currency/link cannot qualify.
- Map source rate limits to `SourceRateLimited` so the cycle stops.
- Add sanitized representative fixtures, malformed-output tests, timeout/429 tests, and opt-in low-volume live tests.
- Document API/CLI version, source commit, license, terms risk, identifier behavior, and whether round trips are genuinely supported.

Register the adapter in `sources/__init__.py` and add an explicit configuration selection policy. Never fall back merely because a valid search found no cheap fare.

