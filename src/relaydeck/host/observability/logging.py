"""One logging configuration for the companion, the gateway and the connector.

Server side only: the phone and browser client never import this module.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from relaydeck.host.observability.exceptions import ExceptionFilter
from relaydeck.host.observability.request_context import RequestIdFilter

FORMAT = "%(asctime)s %(levelname)s %(name)s rid=%(request_id)s %(message)s"


class UTCFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return (
            datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )


def _access_formatter() -> logging.Formatter:
    from uvicorn.logging import AccessFormatter

    class UTCStatusFormatter(AccessFormatter):
        formatTime = UTCFormatter.formatTime

        def formatMessage(self, record: logging.LogRecord) -> str:
            if not isinstance(record.args, tuple) or len(record.args) != 5:
                return UTCFormatter(FORMAT).format(record)
            return super().formatMessage(record)

    return UTCStatusFormatter(
        "%(asctime)s %(levelname)s %(name)s rid=%(request_id)s "
        '%(client_addr)s - "%(request_line)s" %(status_code)s',
        use_colors=False,
    )


def logging_config(level: str = "INFO") -> dict[str, Any]:
    from uvicorn.config import LOGGING_CONFIG

    config = deepcopy(LOGGING_CONFIG)
    config.setdefault("filters", {})["safe_framework"] = {"()": ExceptionFilter}
    config["filters"]["safe_exceptions"] = {"()": ExceptionFilter}
    config["filters"]["request_id"] = {"()": RequestIdFilter}
    config["formatters"]["relaydeck"] = {"()": UTCFormatter, "format": FORMAT}
    config["handlers"]["default"]["formatter"] = "relaydeck"
    config["formatters"]["access"] = {"()": _access_formatter}
    config["handlers"]["default"]["filters"] = ["safe_exceptions", "request_id"]
    config["handlers"]["access"]["filters"] = ["safe_exceptions", "request_id"]
    for name in ("uvicorn.error", "uvicorn.access"):
        config["loggers"][name].setdefault("filters", []).append("safe_framework")
    for name, own_level in (
        ("relaydeck.client.features.terminal.pictures", "INFO"),
        ("relaydeck.lease_lifecycle", "INFO"),
        ("relaydeck.security", "INFO"),
        ("relaydeck.gateway.loop_watch", "WARNING"),
    ):
        config["loggers"][name] = {
            "level": own_level,
            "handlers": ["default"],
            "propagate": False,
        }
    config["handlers"]["fallback"] = {
        **config["handlers"]["default"],
        "level": "WARNING",
    }
    config["root"] = {"level": "WARNING", "handlers": ["fallback"]}
    config["loggers"]["relaydeck"] = {
        "level": level,
        "handlers": ["default"],
        "propagate": False,
    }
    # Gateway children share the application handler without duplicate stream output.
    config["loggers"]["relaydeck.gateway"] = {
        "level": level,
        "handlers": [],
        "propagate": True,
    }
    return config
