# Adding a notification provider

Implement `NotificationProvider.name` and `send(notification, idempotency_key)`. Keep provider configuration in `config.py`, load secrets only from named environment variables, and register it in the CLI notifier factory.

Providers must:

- render deal and health/recovery events;
- escape their markup safely;
- use network timeouts and bounded responses;
- retry only transient failures;
- propagate final failure so the database records it;
- use the supplied idempotency key when the service supports one;
- never expose recipient IDs, credentials, or secret URLs in errors/logs;
- not mark delivery successful until the remote service confirms success.

Add mock-transport unit tests. CI must never need real credentials or send messages. Update `docs/notifications.md` with setup and payload behavior.

