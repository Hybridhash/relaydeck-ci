"""Synthetic connectivity notifications."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum


class ConnectivityState(Enum):
    """Link-layer connectivity. A change is a signal to resync, never a state to display as
    healthy sync."""

    WIFI = "wifi"
    MOBILE = "mobile"
    OFFLINE = "offline"


class ConnectivityPort(ABC):
    """Network-link signal. A change requests an explicit transport resync, because half-open
    sockets would otherwise not error promptly on their own."""

    @abstractmethod
    def current(self) -> ConnectivityState: ...

    @abstractmethod
    def on_change(self, listener: Callable[[ConnectivityState], None]) -> None: ...


@dataclass(frozen=True)
class LinkEvent:
    """One observed connectivity change, in arrival order."""

    seq: int
    state: ConnectivityState


# Bound history without dropping notifications. Sequence numbers remain monotonic across eviction.
LINK_EVENT_HISTORY = 32


class LinkResync:
    """Forward every connectivity change to resync and retain bounded event history."""

    def __init__(
        self,
        port: ConnectivityPort,
        on_link_change: Callable[[ConnectivityState], None],
    ) -> None:
        self._port = port
        self._callback = on_link_change
        self.events: deque[LinkEvent] = deque(maxlen=LINK_EVENT_HISTORY)
        self._seq = 0
        port.on_change(self._handle)

    @property
    def current(self) -> ConnectivityState:
        return self._port.current()

    def _handle(self, state: ConnectivityState) -> None:
        self._seq += 1
        self.events.append(LinkEvent(seq=self._seq, state=state))
        self._callback(state)
