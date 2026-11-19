"""Build destination navigation and load inventory panels with request-generation guards."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import flet as ft

from relaydeck.client.design_system.inputs import SearchField
from relaydeck.client.design_system.loading_spinner import Spinner
from relaydeck.client.platform.flet.events import event_index, event_text

DESTINATIONS: tuple[tuple[str, str, str], ...] = (
    ("agents", "Console", "SMART_TOY"),
    ("terminals", "Terminals", "TERMINAL"),
    ("usage", "Usage", "INSIGHTS"),
    ("settings", "Settings", "SETTINGS"),
)


@ft.component
def InventoryPanel(fetch: Callable[[str], Awaitable[tuple[str, ...]]]) -> Any:
    """Async list with explicit effect deps, cleanup and generation guards."""
    query, set_query = ft.use_state("")
    initial: tuple[str, tuple[str, ...]] = ("loading", ())
    state, set_state = ft.use_state(initial)
    pending: Any = ft.use_ref(None)
    generation: Any = ft.use_ref(0)

    def stop_request() -> None:
        generation.current += 1
        task = pending.current
        if task is not None and not task.done():
            task.cancel()
        pending.current = None

    def start_request() -> None:
        generation.current += 1
        request_generation = generation.current
        set_state(("loading", ()))

        async def load() -> None:
            try:
                items = await fetch(query)
                if generation.current == request_generation:
                    set_state(("ready", items))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - surface as panel error state
                if generation.current == request_generation:
                    set_state(("error", ()))

        pending.current = asyncio.create_task(load())

    ft.use_effect(start_request, dependencies=[query], cleanup=stop_request)

    status, items = state
    return ft.Column(
        controls=[
            SearchField(
                key="inventory-query",
                label="Filter sessions",
                value=query,
                on_change=lambda e: set_query(event_text(e)),
            ),
            Spinner(key="inventory-loading", visible=status == "loading"),
            ft.Text(
                "Could not load sessions" if status == "error" else "",
                key="inventory-error",
            ),
            *[ft.Text(title, key=f"session-{title}") for title in items],
        ]
    )


@ft.component
def AppShell(
    fetch_inventory: Callable[[str], Awaitable[tuple[str, ...]]] | None = None,
) -> Any:
    """Destinations with single-pane content (responsive split later)."""
    destination, set_destination = ft.use_state("agents")

    async def default_fetch(_query: str) -> tuple[str, ...]:
        return ()

    fetch = fetch_inventory or default_fetch
    labels = {d: label for d, label, _ in DESTINATIONS}
    return ft.SafeArea(
        content=ft.Row(
            controls=[
                ft.NavigationRail(
                    key="destinations",
                    selected_index=[d for d, _, _ in DESTINATIONS].index(destination),
                    destinations=[
                        ft.NavigationRailDestination(icon=getattr(ft.Icons, icon), label=label)
                        for _, label, icon in DESTINATIONS
                    ],
                    on_change=lambda e: set_destination(DESTINATIONS[event_index(e)][0]),
                ),
                ft.VerticalDivider(key="shell-divider"),
                ft.Column(
                    controls=[
                        ft.Text(
                            f"RelayDeck — {labels[destination]}",
                            key="destination-title",
                        ),
                        InventoryPanel(key="inventory", fetch=fetch),  # type: ignore[call-arg]
                    ]
                ),
            ]
        )
    )


@ft.component
def App() -> Any:
    return AppShell()
