"""Connects the Changes feature to the app: card summaries, the card tap and the Changes screen."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from typing import Any

import flet as ft

from relaydeck.client.app.workflows.ask import AskRuntime
from relaydeck.client.design_system.theme import agent_identity
from relaydeck.client.features.changes.hooks import use_changes_feed
from relaydeck.client.features.changes.repository import ChangesRepository
from relaydeck.client.features.changes.review_state import ReviewStateStore
from relaydeck.client.models.inventory_selectors import card_title
from relaydeck.client.models.routing import CHANGES_SECTIONS
from relaydeck.client.screens.changes.screen import ChangesScreen
from relaydeck.contracts.git import WorktreeSummary
from relaydeck.domain.models import scope_key, unscope_key

SECTION = "changes"  # the first level; a phone file adds "file", a commit "commit", "commit-file"
FIRST_CARDS = 24  # asked for until the list says which cards it shows
WINDOW_SETTLE_S = 0.15  # a list still moving is not asked about at every step


@dataclass(frozen=True, slots=True)
class ChangesWiring:
    summaries: dict[str, WorktreeSummary]
    on_changes: Callable[[str], None]
    screen: Any  # the Changes screen when the route asks for it, else None
    reviews: ReviewStateStore
    stale: frozenset[str] = frozenset()  # agents whose latest summary poll failed
    on_window: Callable[[tuple[str, ...]], None] = lambda _keys: None  # the cards the list shows


def use_changes_wiring(
    *,
    transport: Any,
    pager: Any,
    destination: str,
    selection: str,
    section: str,
    step: int = 0,
    foreground: bool,
    wide: bool,
    phone: bool = True,
    navigate: Callable[..., Any],
    account: str = "",
    connected: bool | None = None,
    ask_runtime: AskRuntime | None = None,
    names: Mapping[str, str] | None = None,  # agent display names, as the cards show them
) -> ChangesWiring:
    source_ref: Any = ft.use_ref(None)
    if source_ref.current is None or source_ref.current[0] is not transport:
        source_ref.current = (transport, ChangesRepository(transport) if transport else None)
    source: ChangesRepository | None = source_ref.current[1]
    # Viewed marks live for the app session: a new sign-in (another transport) or another
    # account starts with none.
    reviews_ref: Any = ft.use_ref(None)
    if reviews_ref.current is None:
        reviews_ref.current = [(transport, account), ReviewStateStore()]
    elif reviews_ref.current[0][0] is not transport or reviews_ref.current[0][1] != account:
        reviews_ref.current[1].clear_all()
        reviews_ref.current[0] = (transport, account)
    reviews: ReviewStateStore = reviews_ref.current[1]
    clipboard_ref: Any = ft.use_ref(None)  # one service for the wiring, built on first screen

    rows = tuple(pager.visible_rows) if pager is not None else ()
    everyone = [scope_key(row["host_id"], row["terminal_id"]) for row in rows]
    # Summaries are asked for the cards the list shows, plus a screen either side, not for
    # every agent there is.
    unset: tuple[str, ...] | None = None
    window, set_window = ft.use_state(unset)
    shown: Any = ft.use_ref(None)
    waiting: Any = ft.use_ref(None)

    def on_window(cards: tuple[str, ...]) -> None:
        if waiting.current is not None:
            waiting.current.cancel()
            waiting.current = None

        def apply() -> None:
            waiting.current = None
            if cards != shown.current:
                shown.current = cards
                set_window(cards)

        if shown.current is None:
            apply()
        else:
            waiting.current = asyncio.get_running_loop().call_later(WINDOW_SETTLE_S, apply)

    def stop_waiting() -> None:
        if waiting.current is not None:
            waiting.current.cancel()
            waiting.current = None

    ft.use_effect(lambda: None, dependencies=[], cleanup=stop_waiting)
    if window is None:
        wanted = frozenset(everyone[:FIRST_CARDS])
    else:
        wanted = frozenset(window)
    picked = [(key, row) for key, row in zip(everyone, rows, strict=True) if key in wanted]
    keys = [key for key, _ in picked]
    statuses = {key: str(row.get("status", "")) for key, row in picked}
    on_screen = destination == "agents" and section in CHANGES_SECTIONS and bool(selection)
    if on_screen and pager is not None:
        open_row = pager.rows.get(selection)
        if open_row is not None:
            statuses[selection] = str(open_row.get("status", ""))
    feed = use_changes_feed(
        source,
        keys,
        statuses,
        # The phone shows the screen instead of the list; the screen has its own check.
        console=destination == "agents" and (wide or not on_screen),
        foreground=foreground,
        viewer=selection if on_screen else None,
        connected=connected,
    )
    summaries = feed.summaries

    def on_changes(key: str) -> None:
        navigate("agents", key, section=SECTION)

    screen = None
    split = unscope_key(selection) if selection else None
    if on_screen and split is not None and source is not None:
        summary = summaries.get(selection)
        row = pager.rows.get(selection) if pager is not None else None
        title = card_title(row, (names or {}).get(selection)) if row is not None else "Agent"
        kind = str((row or {}).get("agent") or "")
        context_line = f"{title} · {agent_identity(kind, True).label}" if kind else title
        if clipboard_ref.current is None:
            clipboard_ref.current = ft.Clipboard()
        clipboard = clipboard_ref.current

        def copy(text: str) -> None:
            ft.context.page.run_task(clipboard.set, text)

        identity = ask_runtime.gate(selection)[0] if ask_runtime else None
        screen = ChangesScreen(  # type: ignore[call-arg]
            split[0],
            split[1],
            summary.worktree_id if summary is not None and summary.worktree_id else None,
            title,
            source,
            key=f"changes-{selection}",
            summary=summary,
            refresh_failed=selection in feed.stale,
            checked=lambda: feed.checked.get(selection),
            on_recheck=lambda: feed.recheck(selection),
            reviews=reviews,
            server=str(getattr(transport, "base_url", "")),
            account=account,
            wide=wide,
            phone=phone,
            foreground=foreground,
            section=section,
            step=step,
            on_level=lambda level, record, at: navigate(
                "agents", selection, section=level, step=at, record=record
            ),
            on_back=lambda: navigate("agents"),
            on_terminal=ask_runtime.terminal
            if ask_runtime
            else lambda: navigate("agents", selection),
            on_copy=copy,
            context_line=context_line,
            on_ask=partial(ask_runtime.start, identity=identity)
            if ask_runtime is not None
            else None,
            ask_identity=identity,
            ask_status=ask_runtime.gate(selection)[1]
            if ask_runtime
            else statuses.get(selection, "unknown"),
            ask_unavailable=ask_runtime.gate(selection)[2]
            if ask_runtime
            else "Questions are unavailable.",
            bookmark=ask_runtime.bookmark(selection) if ask_runtime else None,
        )
    return ChangesWiring(summaries, on_changes, screen, reviews, feed.stale, on_window)
