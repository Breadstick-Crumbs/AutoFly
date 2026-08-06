import logging

from autofly.logging import configure_logging


def test_transport_loggers_never_emit_request_urls_at_info() -> None:
    configure_logging(verbose=True)
    assert logging.getLogger("httpx").level >= logging.WARNING
    assert logging.getLogger("httpcore").level >= logging.WARNING
