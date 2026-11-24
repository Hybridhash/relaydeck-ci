"""Deliver due generic hints with expiry, authorization and per-device retry handling."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from enum import Enum
from typing import Any

from relaydeck.gateway.features.notifications.model import PushDenied
from relaydeck.gateway.features.notifications.ports import OutboxStore


class ProviderOutcome(Enum):
    SENT = "sent"
    RETRYABLE = "retryable"
    PERMANENT = "permanent"


class ProviderPort(ABC):
    """Push provider interface; production FCM is explicitly configured."""

    @abstractmethod
    def send(self, token: str, payload: dict[str, str]) -> ProviderOutcome:
        """Deliver one hint. RETRYABLE = try again within TTL;
        PERMANENT = token dead, tombstone the device."""


def pump_outbox(
    store: OutboxStore,
    provider: ProviderPort,
    now: int | None = None,
    limit: int = 100,
    *,
    stop: Callable[[], bool] = lambda: False,
    authorized: Callable[[dict[str, Any]], bool] = lambda row: True,
) -> dict[str, int]:
    """Sweep expired hints and deliver due rows in per-device FIFO order. Retry provider faults
    within TTL; permanent failures tombstone the device and drop its pending hints.
    """
    at = store.now() if now is None else now
    report = {"sent": 0, "retried": 0, "dropped": 0}
    report["dropped"] += store.sweep(at)["dropped"]
    for row in store.due(at, limit):
        if stop():
            break
        notification_id = str(row["notification_id"])
        account = str(row["account"])
        device_id = str(row["device_id"])
        token = store.token_for(account, device_id)
        if token is None or not authorized(row):
            # Every due row observed with a dead token ends this pump
            # dropped: either just now, or by an invalid-token cascade
            # from an earlier row of the same device in this loop.
            store.drop_notification(notification_id, "revoked", at)
            report["dropped"] += 1
            continue
        try:
            payload = store.hint_payload(notification_id)
        except PushDenied as exc:
            if store.drop_notification(notification_id, exc.code, at):
                report["dropped"] += 1
            continue
        try:
            outcome = provider.send(token, payload)
        except Exception as exc:  # noqa: BLE001 - provider faults retry
            outcome = ProviderOutcome.RETRYABLE
            error = f"provider raised: {type(exc).__name__}"
        else:
            error = "provider retryable"
        if outcome is ProviderOutcome.SENT:
            if store.mark_sent(notification_id, at):
                report["sent"] += 1
            else:
                report["dropped"] += 1
        elif outcome is ProviderOutcome.PERMANENT:
            store.mark_token_invalid(account, device_id, at)
            report["dropped"] += 1
        else:
            status = store.mark_failed(notification_id, error, at)
            report["retried" if status == "pending" else "dropped"] += 1
    return report
