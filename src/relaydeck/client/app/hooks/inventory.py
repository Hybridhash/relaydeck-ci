"""Subscribe the root to inventory state and derive visible rows."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable
from typing import Any

import flet as ft

from relaydeck.client.app.state.sync import SyncController
from relaydeck.client.features.inventory.paging import InventoryFilters, InventoryPager
from relaydeck.client.features.inventory.source import InventorySource
from relaydeck.client.features.sync.inventory_updates import apply_rows
from relaydeck.domain.models import ConnectionState


def use_inventory(
    *,
    account: str,
    app_state: Any,
    controller: SyncController,
    foreground: bool,
    transport: InventorySource,
) -> tuple[Any, ...]:
    """Paged agent inventory: pager, supervised tasks, refresh/cancel/release effects."""
    inventory_tick, set_inventory_tick = ft.use_state(0)

    inventory_account: Any = ft.use_ref(account)

    inventory_account.current = account

    inventory_alive: Any = ft.use_ref(True)

    inventory_filters, set_inventory_filters = ft.use_state(InventoryFilters())

    pager_box: Any = ft.use_ref(None)

    inventory_tasks: Any = ft.use_ref(set())
    applied: Any = ft.use_ref(None)
    read_owner: Any = ft.use_ref(None)
    live_context: Any = ft.use_ref(None)
    deltas = getattr(controller, "inventory_updates", None)

    pager_owner: Any = ft.use_ref(None)
    if pager_owner.current is None or pager_owner.current[0] is not transport:
        if pager_box.current is not None:
            pager_box.current.clear()
        for pending in tuple(inventory_tasks.current):
            pending.cancel()
        inventory_tasks.current.clear()
        applied.current = None
        read_owner.current = None
        pager_box.current = None
        pager_owner.current = (transport, object())
    owner_token = pager_owner.current[1]

    if pager_box.current is None and callable(getattr(transport, "agents", None)):
        pager_box.current = InventoryPager(
            transport, lambda: set_inventory_tick(time.monotonic_ns())
        )

    pager: InventoryPager | None = pager_box.current

    inventory_hosts = {
        host.id: host.epoch
        for host in app_state.inventory.hosts.values()
        if not app_state.revoked
        and (app_state.granted_hosts is None or host.id in app_state.granted_hosts)
    }
    context = (
        account,
        foreground,
        inventory_filters,
        tuple(sorted(inventory_hosts.items())),
        app_state.connection.state == ConnectionState.LIVE,
        deltas,
        owner_token,
    )
    live_context.current = context

    def inventory_task(coro: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(coro)
        inventory_tasks.current.add(task)

        def finished(done: asyncio.Future[Any]) -> None:
            inventory_tasks.current.discard(done)
            if (
                not done.cancelled()
                and done.exception() is not None
                and inventory_alive.current
                and inventory_account.current == account
                and pager_box.current is pager
                and pager_box.current is not None
                and pager_box.current.filters == inventory_filters
            ):
                pager_box.current.error = "Inventory request failed. Refresh to retry."
                pager_box.current.changed()

        task.add_done_callback(finished)

    def cancel_inventory() -> None:
        read_owner.current = None
        if inventory_tasks.current or (pager is not None and pager.busy):
            applied.current = None
        if pager is not None:
            pager.invalidate()
        for task in tuple(inventory_tasks.current):
            task.cancel()
        inventory_tasks.current.clear()

    async def read_inventory(proof: tuple[Any, ...]) -> None:
        if (
            pager is None
            or live_context.current != context
            or inventory_account.current != account
            or not inventory_alive.current
            or controller.state.revoked
        ):
            return
        applied.current = None
        owner = object()
        read_owner.current = owner
        state = controller.state
        try:
            await pager.refresh(account, inventory_hosts, inventory_filters)
            if (
                read_owner.current is owner
                and live_context.current == context
                and inventory_account.current == account
                and inventory_alive.current
                and controller.state is state
                and not state.revoked
                and state.connection.last_sequence == proof[2]
                and (deltas is None or deltas.version == proof[1])
                and pager.account == account
                and pager.filters == inventory_filters
                and not pager.busy
                and not pager.error
            ):
                applied.current = proof
        finally:
            if read_owner.current is owner:
                read_owner.current = None

    def refresh_inventory(*, incremental: bool = False) -> None:
        if (
            not foreground
            or pager is None
            or not inventory_alive.current
            or inventory_account.current != account
        ):
            return
        if controller.state.revoked:
            applied.current = None
            pager.clear()
            set_inventory_tick(time.monotonic_ns())
            return
        previous = applied.current
        proof = (
            context,
            deltas.version if deltas is not None else None,
            app_state.connection.last_sequence,
        )
        if (
            incremental
            and previous is not None
            and previous[0] == context
            and deltas is not None
            and not pager.error
        ):
            updates = deltas.since(previous[1], previous[2], app_state.connection.last_sequence)
            if updates is not None and not inventory_filters.status and not inventory_filters.query:
                if apply_rows(pager.rows, updates):
                    applied.current = proof
                    if updates:
                        pager.changed()
                    return
        applied.current = None
        inventory_task(read_inventory(proof))

    ft.use_effect(
        lambda: refresh_inventory(incremental=True),
        dependencies=[
            owner_token,
            account,
            foreground,
            tuple(sorted(inventory_hosts.items())),
            inventory_filters,
            app_state.connection.last_sequence,
            app_state.connection.state == ConnectionState.LIVE,
            app_state.revoked,
            deltas.version if deltas is not None else None,
        ],
        cleanup=cancel_inventory,
    )

    def release_inventory() -> None:
        inventory_alive.current = False
        cancel_inventory()
        if pager is not None:
            pager.clear()

    def hold_inventory() -> None:
        inventory_alive.current = True

    ft.use_effect(hold_inventory, dependencies=[], cleanup=release_inventory)

    def more_inventory() -> None:
        if pager is not None and inventory_alive.current and inventory_account.current == account:

            async def load() -> None:
                await pager.load_more()
                if pager.error:
                    applied.current = None
                if "stale_epoch" in pager.error and inventory_account.current == account:
                    await read_inventory(
                        (
                            context,
                            deltas.version if deltas is not None else None,
                            app_state.connection.last_sequence,
                        )
                    )

            inventory_task(load())

    return (
        inventory_account,
        inventory_alive,
        inventory_filters,
        inventory_hosts,
        inventory_task,
        inventory_tick,
        more_inventory,
        pager,
        refresh_inventory,
        release_inventory,
        set_inventory_filters,
    )
