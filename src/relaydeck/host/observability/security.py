"""Fixed security events emitted after an operation's state transition completes."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from contextvars import ContextVar
from enum import StrEnum
from functools import wraps
from typing import ParamSpec, TypeVar

LOG = logging.getLogger("relaydeck.security")
P = ParamSpec("P")
T = TypeVar("T")
_DEPTH: ContextVar[int] = ContextVar("security_operation_depth", default=0)


class Event(StrEnum):
    PAIRING_REQUESTED = "pairing_requested"
    PAIRING_DECISION = "pairing_decision"
    PAIRING_REDEMPTION = "pairing_redemption"
    GRANT_ISSUED = "grant_issued"
    SESSION_REVOKED = "session_revoked"
    DEVICE_REVOKED = "device_revoked"
    ACCOUNT_REVOKED = "account_revoked"
    HOST_REVOKED = "host_revoked"
    HOST_GRANTS_REVOKED = "host_grants_revoked"


class Outcome(StrEnum):
    COMPLETED = "completed"
    APPROVED = "approved"
    DENIED = "denied"
    NOOP = "noop"
    REJECTED = "rejected"
    FAILED = "failed"


class SecurityEvents:
    """Bound repeated failures by fixed event vocabulary, never by credential or account."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._last: dict[tuple[Event, Outcome], float] = {}
        self._lock = threading.Lock()

    def emit(self, event: Event, outcome: Outcome) -> None:
        if type(event) is not Event or type(outcome) is not Outcome:
            raise ValueError("Unknown security event")
        if outcome in (Outcome.REJECTED, Outcome.FAILED):
            with self._lock:
                now = self.clock()
                key = event, outcome
                if now - self._last.get(key, float("-inf")) < 1:
                    return
                self._last[key] = now
        try:
            LOG.log(
                logging.WARNING if outcome == Outcome.FAILED else logging.INFO,
                "security_event event=%s outcome=%s",
                event.value,
                outcome.value,
            )
        except Exception:  # noqa: BLE001, S110 - a broken sink cannot undo a transition
            pass


EVENTS = SecurityEvents()


def completed(event: Event) -> None:
    if _DEPTH.get() == 0:
        EVENTS.emit(event, Outcome.COMPLETED)


def operation(
    event: Event,
    *,
    denied: tuple[type[Exception], ...] = (),
    none_is_noop: bool = False,
    decision: bool = False,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Nested revocations belong to the outer operation; return follows durable commit."""

    def decorate(function: Callable[P, T]) -> Callable[P, T]:
        @wraps(function)
        def run(*args: P.args, **kwargs: P.kwargs) -> T:
            depth = _DEPTH.get()
            token = _DEPTH.set(depth + 1)
            try:
                result = function(*args, **kwargs)
            except Exception as exc:
                if depth == 0:
                    EVENTS.emit(
                        event, Outcome.REJECTED if isinstance(exc, denied) else Outcome.FAILED
                    )
                raise
            else:
                if depth == 0:
                    noop = (
                        (result is None and none_is_noop)
                        or result is False
                        or type(result) is int
                        and result == 0
                        or isinstance(result, tuple)
                        and len(result) == 0
                    )
                    outcome = Outcome.NOOP if noop else Outcome.COMPLETED
                    if decision:
                        outcome = (
                            Outcome.APPROVED if kwargs.get("approve") is True else Outcome.DENIED
                        )
                    EVENTS.emit(event, outcome)
                return result
            finally:
                _DEPTH.reset(token)

        return run

    return decorate
