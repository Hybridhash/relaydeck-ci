"""Terminal ownership routes: publish each host's terminal epochs and release leases."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from relaydeck.gateway.app.state import GatewayState

# The reconciler sleeps until the nearest lease/grace expiry that can flip a
# route's mode, or until a publish wakes it; the max bounds the wait for
# changes no expiry predicts, and the min is the retry cadence after a
# publish that did not reach every tunnel.
RECONCILE_MIN_S = 1.0
RECONCILE_MAX_S = 30.0
RECONCILE_FLOOR_S = 0.05

_waiters: set[asyncio.Event] = set()
# Bumped by every external wake (a publish from a lease route, a shortened
# deadline). The reconciler compares it across its scan: a change during an
# awaited publish means the routes/deadlines it just read may be stale.
_generation = 0


def wake_terminal_routes() -> None:
    """Wake the reconciler after a lease action changed ownership routes."""
    global _generation
    _generation += 1
    for event in tuple(_waiters):
        event.set()


async def _wait_reconcile(delay: float) -> None:
    changed = asyncio.Event()
    _waiters.add(changed)
    waiting = {
        asyncio.create_task(asyncio.sleep(delay)),
        asyncio.create_task(changed.wait()),
    }
    try:
        done, _pending = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        _waiters.discard(changed)
        for task in waiting:
            task.cancel()
        await asyncio.gather(*waiting, return_exceptions=True)


async def release_lease_and_close(
    state: GatewayState, lease_id: str, account: str, device_id: str
) -> None:
    """Detach one lease (the attachment and remote process survive) and
    sever its sockets. Holder-only: unknown, foreign, or other-device
    leases are BackendError not_found (unlike attachment close, which
    is account-scoped)."""
    attachment = state.backend.release_lease(lease_id, account, device_id)
    await state.sockets.close_lease(lease_id, "detached")
    await publish_terminal_routes(state, attachment.host_id)


async def publish_terminal_routes(state: GatewayState, host_id: str, *, wake: bool = True) -> int:
    """Gateway-only ownership mode: observer streams cannot hold Mac sizing."""
    if wake:
        wake_terminal_routes()
    record = state.hosts.record_for_host(host_id)
    if record is not None:
        return await state.sockets.notify_host(
            host_id,
            json.dumps(
                {
                    "kind": "terminal.epochs",
                    "host_id": host_id,
                    "epochs": state.backend.epochs_for_host(host_id, record.account),
                }
            ),
            binding=record,
        )
    return 0


async def reconcile_terminal_routes(state: GatewayState) -> None:
    """Expiry also tears down upstream ownership with no live browser socket."""
    previous: dict[str, list[dict[str, str]]] = {}
    while True:
        scan_generation = _generation
        records = state.hosts.hosts()
        scanned: dict[str, list[dict[str, Any]]] = {}
        retry_publish = False
        for record in records:
            routes = state.backend.epochs_for_host(record.host_id, record.account)
            scanned[record.host_id] = routes
            if previous.get(record.host_id) != routes:
                notified = await publish_terminal_routes(state, record.host_id, wake=False)
                if notified >= state.sockets.host_count(record.host_id, record):
                    previous[record.host_id] = routes
                else:
                    retry_publish = True
        # Register the wait without yielding after revalidation, preserving any absolute deadline
        # already due.
        stale = _generation != scan_generation or state.hosts.hosts() != records
        deadlines: list[int] = []
        has_unknown_expiry = False
        for record in records:
            deadline_before = state.backend.next_epoch_change_ms(record.host_id, record.account)
            if state.backend.epochs_for_host(record.host_id, record.account) != scanned.get(
                record.host_id
            ):
                stale = True
            deadline_after = state.backend.next_epoch_change_ms(record.host_id, record.account)
            relevant = [
                deadline for deadline in (deadline_before, deadline_after) if deadline is not None
            ]
            if relevant:
                deadlines.append(min(relevant))
            else:
                has_unknown_expiry = True
        decision_now_ms = state.now_ms()
        if any(deadline <= decision_now_ms for deadline in deadlines):
            stale = True
        if stale:
            continue
        delay = RECONCILE_MAX_S
        if deadlines:
            expiry_delay = max(RECONCILE_FLOOR_S, (min(deadlines) - decision_now_ms) / 1000)
            delay = min(delay, expiry_delay) if has_unknown_expiry else expiry_delay
        if retry_publish:
            delay = min(delay, RECONCILE_MIN_S)  # retry the publish promptly
        await _wait_reconcile(delay)
