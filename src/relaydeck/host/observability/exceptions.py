"""Content-free exception records, independent of handler and formatter choice."""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable
from typing import Any

from relaydeck.host.observability.request_context import current_request_id

_CATEGORIES: tuple[tuple[type[BaseException], str], ...] = (
    (TimeoutError, "timeout"),
    (PermissionError, "permission"),
    (OSError, "io"),
    (ValueError, "invalid"),
    (BaseExceptionGroup, "group"),
)


def exception_category(exc: BaseException | None) -> str:
    for kind, label in _CATEGORIES:
        if isinstance(exc, kind):
            return label
    return "unexpected"


def _peer_closed(exc: BaseException | None, depth: int = 0) -> bool:
    if exc is None or depth > 8:
        return False
    if isinstance(exc, BaseExceptionGroup):
        return bool(exc.exceptions) and all(_peer_closed(e, depth + 1) for e in exc.exceptions)
    return any(
        cls.__name__ in {"WebSocketDisconnect", "ClientDisconnected", "ConnectionClosed"}
        for cls in type(exc).__mro__
    )


def _unsafe_arguments(value: object) -> bool:
    pending = [value]
    for _ in range(64):
        if not pending:
            return False
        item = pending.pop()
        if isinstance(item, BaseException):
            return True
        if type(item) is tuple or type(item) is list:
            if len(item) > 64:
                return True
            pending.extend(item)
        elif type(item) is dict:
            if len(item) > 64:
                return True
            pending.extend(item.keys())
            pending.extend(item.values())
        elif item is not None and type(item) not in (str, int, float, bool, bytes):
            return True
    return True


_EVENT_TEMPLATE = re.compile(r"[a-z][a-z0-9_]*( [a-z][a-z0-9_]*=%[sdr])*")
_TOKEN = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_FRAMEWORK_EVENTS = {
    "Started server process [%d]": "server_started",
    "Finished server process [%d]": "server_stopped",
    "Uvicorn running on socket %s (Press CTRL+C to quit)": "server_listening",
    "Uvicorn running on unix socket %s (Press CTRL+C to quit)": "server_listening",
    "Uvicorn running on %s://%s:%d (Press CTRL+C to quit)": "server_listening",
    "Uvicorn running on %s://[%s]:%d (Press CTRL+C to quit)": "server_listening",
    "Waiting for application startup.": "application_starting",
    "Application startup complete.": "application_started",
    "Waiting for application shutdown.": "application_stopping",
    "Application shutdown complete.": "application_stopped",
    "Shutting down": "server_stopping",
    '%s - "WebSocket %s" [accepted]': "websocket_accepted",
}


def _framework_event(record: logging.LogRecord) -> None:
    template = record.msg if type(record.msg) is str else ""
    arguments = record.args
    record.msg = _FRAMEWORK_EVENTS.get(template, "framework_record")
    record.args = ()
    if template == '%s - "WebSocket %s" 403':
        record.msg, record.args = "websocket_response status=%d", (403,)
    elif template in {'%s - "WebSocket %s" %d', '%s - "%s %s HTTP/%s" %d'}:
        status = arguments[-1] if type(arguments) is tuple and arguments else None
        if type(status) is int and 100 <= status <= 599:
            event = "websocket_response" if "WebSocket" in template else "http_response"
            record.msg, record.args = event + " status=%d", (status,)
    record._safe_framework = True


def _framework_name(name: str) -> bool:
    return name.split(".", 1)[0] in {"uvicorn", "flet", "asyncio", "starlette"} or name.startswith(
        "flet_"
    )


def _safe_template(record: logging.LogRecord) -> bool:
    """Retain structured event names, never arbitrary messages or formatted payloads."""
    if type(record.msg) is not str or len(record.msg) > 1024:
        return False
    if _EVENT_TEMPLATE.fullmatch(record.msg) is None or type(record.args) is not tuple:
        return False
    fields = record.msg.split()[1:]
    if len(fields) != len(record.args):
        return False
    for field, value in zip(fields, record.args, strict=True):
        if type(value) is str:
            if field.endswith("%d") or _TOKEN.fullmatch(value) is None:
                return False
        elif type(value) not in (bool, int, float):
            return False
        elif type(value) is float and not math.isfinite(value):
            return False
    return True


def sanitize(record: logging.LogRecord) -> None:
    """Discard exception payloads before any handler can interpolate or cache them."""
    exc = record.exc_info[1] if record.exc_info else None
    argument_error = _unsafe_arguments(record.args)
    framework_record = _framework_name(record.name)
    if framework_record:
        record.__dict__.pop("color_message", None)
        record.__dict__.pop("message", None)
    framework_error = (
        record.levelno >= logging.ERROR
        and framework_record
        and not getattr(record, "_safe_exception", False)
    )
    exception_record = (
        record.exc_info is not None
        or record.exc_text
        or record.stack_info
        or framework_error
        or isinstance(record.msg, BaseException)
    )
    if exception_record or argument_error:
        if (
            record.name == "uvicorn.error"
            and isinstance(record.msg, str)
            and record.msg.startswith("Exception in ASGI application")
            and _peer_closed(exc)
        ):
            member = exc
            while isinstance(member, BaseExceptionGroup):
                member = member.exceptions[0]
            code = getattr(member, "code", None)
            record.msg = "flet_ws_closed code=%s"
            record.args = (code if type(code) is int else None,)
            record.levelno, record.levelname = logging.INFO, "INFO"
        elif exception_record:
            category = exception_category(exc)
            if (
                not argument_error
                and not framework_record
                and isinstance(record.args, tuple)
                and _safe_template(record)
            ):
                record.msg += " category=%s"
                record.args = (*record.args, category)
            else:
                record.msg = "exception category=%s"
                record.args = (category,)
        else:
            record.msg = "log_arguments_dropped"
            record.args = ()
        record._safe_exception = True
        if framework_record:
            record._safe_framework = True
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        record.__dict__.pop("message", None)
    elif framework_record and not getattr(record, "_safe_framework", False):
        _framework_event(record)


class ExceptionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        sanitize(record)
        return True


class _SafeFactory:
    def __init__(self, previous: Callable[..., logging.LogRecord]) -> None:
        self.previous = previous

    def __call__(self, *args: Any, **kwargs: Any) -> logging.LogRecord:
        record = self.previous(*args, **kwargs)
        sanitize(record)
        if _framework_name(record.name):
            logger = logging.getLogger(record.name)
            if not any(isinstance(filter_, ExceptionFilter) for filter_ in logger.filters):
                # Logger filters run after makeRecord merges formatter-specific extras.
                logger.addFilter(ExceptionFilter())
        # Root/custom formatters may use correlation without the shared filter.
        record.request_id = current_request_id()
        return record


def install_exception_safety() -> None:
    """Install once per process; dictConfig/fileConfig do not replace the factory."""
    previous = logging.getLogRecordFactory()
    if not isinstance(previous, _SafeFactory):
        logging.setLogRecordFactory(_SafeFactory(previous))
