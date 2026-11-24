"""The request ID of the HTTP request being served, and the log filter that attaches it."""

from __future__ import annotations

import logging
from contextvars import ContextVar

current: ContextVar[str] = ContextVar("relaydeck_request_id", default="-")


def current_request_id() -> str:
    return current.get()


class RequestIdFilter(logging.Filter):
    """Give every record a `request_id`, so one formatter serves requests and background logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current.get()
        return True
