"""Command status recovery and explicit review of proven outcomes."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import flet as ft

from relaydeck.client.design_system.buttons import Button
from relaydeck.client.features.commands.api import PendingCommands
from relaydeck.client.models.commands import wording as words
from relaydeck.client.models.commands.pending import FINAL, REVIEW_GRACE_MS
from relaydeck.client.ports.commands import CommandStatusSource
from relaydeck.domain.models import scope_key

# Pinned Flet has no max-height, so the opened review list sizes to
# content for 1-2 rows and only then takes the bounded 220px scrollable
# reservation.
_LIST_MAX_HEIGHT = 220
_NATURAL_ROWS = 2
_NOTICE_SECONDS = 5
_KIND_LABELS = {
    "agent.start": "Starting an agent",
    "agent.rename": "Renaming an agent",
    "shell.create": "Opening a terminal",
    "tab.create": "Opening a terminal",
}


@ft.component
def CommandRecovery(
    journal: PendingCommands,
    source: CommandStatusSource,
    granted: frozenset[str],
    go: Any,
    names: Mapping[str, str] | None = None,
) -> Any:
    _, refresh = ft.use_state(0)
    opened, set_opened = ft.use_state(False)
    notice, set_notice = ft.use_state("")
    confirming, set_confirming = ft.use_state(None)
    done, set_done = ft.use_state("")
    done_timer: Any = ft.use_ref(None)
    unsub: Any = ft.use_ref(None)
    task: Any = ft.use_ref(None)
    alive: Any = ft.use_ref(True)
    timer: Any = ft.use_ref(None)

    def subscribe() -> None:
        alive.current = True
        unsub.current = journal.subscribe(lambda: refresh(lambda value: value + 1))

    def cleanup() -> None:
        alive.current = False
        if unsub.current is not None:
            unsub.current()
        if task.current is not None:
            task.current.cancel()

    ft.use_effect(subscribe, dependencies=[journal], cleanup=cleanup)

    def run(operation: Any, after: str = "", failure: str = words.STATUS_UNAVAILABLE) -> None:
        if task.current is not None and not task.current.done():
            operation.close()
            return

        async def work() -> None:
            try:
                await operation
                if alive.current:
                    set_notice("")
                    set_confirming(None)
                    set_done(after)
            except Exception:
                if alive.current:
                    set_notice(failure)

        task.current = asyncio.create_task(work())

    def expire_done() -> None:
        if done:
            done_timer.current = asyncio.get_running_loop().call_later(
                _NOTICE_SECONDS, lambda: set_done("")
            )

    def stop_expire() -> None:
        if done_timer.current is not None:
            done_timer.current.cancel()
            done_timer.current = None

    ft.use_effect(expire_done, dependencies=[done], cleanup=stop_expire)

    now = journal.clock()
    records = list(journal.records.values())
    rows = [row for row in records if journal.needs_review(row, now)]
    waiting = [row.get("created_ms", 0) + REVIEW_GRACE_MS for row in records if row not in rows]
    due = min(waiting, default=0)

    def recheck() -> None:
        # Re-render when the oldest unconfirmed operation crosses the grace period.
        if due:
            timer.current = asyncio.get_running_loop().call_later(
                max(0, due - now) / 1000, lambda: refresh(lambda value: value + 1)
            )

    def stop_recheck() -> None:
        if timer.current is not None:
            timer.current.cancel()
            timer.current = None

    ft.use_effect(recheck, dependencies=[due], cleanup=stop_recheck)
    if not rows and not journal.error:
        if done:
            return ft.Container(
                key="command-recovery",
                padding=8,
                content=ft.Text(done, key="command-recovery-notice"),
            )
        return ft.Container(key="command-recovery-empty", height=0)
    prompts = sum(1 for row in rows if row["kind"] == "agent.prompt")
    controls: list[Any] = [
        ft.Row(
            wrap=True,
            controls=[
                ft.Text(words.summary(prompts, len(rows) - prompts)),
                Button(
                    "Hide" if opened else "Review",
                    key="command-recovery-toggle",
                    height=48,
                    on_click=lambda e: set_opened(not opened),
                ),
                Button(
                    "Check now",
                    key="command-recovery-check",
                    height=48,
                    on_click=lambda e: run(journal.reconcile(source.command_status, granted)),
                ),
            ],
        )
    ]
    if journal.error or notice:
        controls.append(ft.Text(journal.error or notice, key="command-recovery-error"))
    if done:
        controls.append(ft.Text(done, key="command-recovery-notice"))
    if opened:
        items: list[Any] = []
        for row in rows:
            host, command_id, created = row["host"], row["id"], row["created"]
            if row["kind"] == "agent.prompt":
                items.append(
                    _prompt_item(
                        row,
                        names or {},
                        granted,
                        go,
                        confirming == (host, command_id),
                        set_confirming,
                        run,
                        journal,
                        source,
                    )
                )
                continue
            resource = (
                created.get("agent_id") or created.get("tab_id") or created.get("workspace_id")
            )
            destination = (
                "agents"
                if created.get("agent_id")
                else "terminals"
                if created.get("tab_id")
                else "workspaces"
            )
            actions: list[Any] = []
            if resource:
                route = f"/{destination}/{scope_key(host, resource)}"
                actions.append(
                    Button(
                        "Open created resources",
                        height=48,
                        disabled=host not in granted,
                        on_click=lambda e, route=route: go(route),
                    )
                )
            if row["outcome"] in FINAL:
                actions.append(
                    Button(
                        "Reviewed",
                        height=48,
                        on_click=lambda e, host=host, command_id=command_id: run(
                            journal.acknowledge(host, command_id)
                        ),
                    )
                )
            items.append(
                ft.Column(
                    key=f"recovered-{host}-{command_id}",
                    controls=[
                        ft.Text(f"{_KIND_LABELS.get(row['kind'], 'A request')}: {row['outcome']}"),
                        ft.Text(
                            "Some resources were created; review them before a new creation.",
                            visible=bool(created and row["outcome"] != "succeeded"),
                        ),
                        ft.Row(wrap=True, controls=actions),
                    ],
                )
            )
        if len(items) <= _NATURAL_ROWS:
            controls.append(ft.Column(key="command-recovery-list", controls=items))
        else:
            controls.append(
                ft.Column(
                    key="command-recovery-list",
                    height=_LIST_MAX_HEIGHT,
                    scroll=ft.ScrollMode.AUTO,
                    controls=items,
                )
            )
    return ft.Container(key="command-recovery", padding=8, content=ft.Column(controls=controls))


def _prompt_item(
    row: dict[str, Any],
    names: Mapping[str, str],
    granted: frozenset[str],
    go: Any,
    confirming: bool,
    set_confirming: Any,
    run: Any,
    journal: PendingCommands,
    source: CommandStatusSource,
) -> Any:
    host, command_id = row["host"], row["id"]
    agent_id = row["target"]["agent_id"]
    agent = names.get(scope_key(host, agent_id), "")
    key = f"recovered-{host}-{command_id}"
    if confirming:
        return ft.Column(
            key=key,
            controls=[
                ft.Text(words.DISMISS_TITLE, weight=ft.FontWeight.BOLD),
                ft.Text(words.dismiss_body(agent)),
                ft.Row(
                    wrap=True,
                    controls=[
                        Button(
                            "Cancel",
                            key="command-recovery-dismiss-cancel",
                            height=48,
                            on_click=lambda e: set_confirming(None),
                        ),
                        Button(
                            "Dismiss",
                            key="command-recovery-dismiss-confirm",
                            height=48,
                            on_click=lambda e: run(
                                journal.dismiss(host, command_id, agent_id),
                                words.dismissed(agent),
                                words.DISMISS_UNAVAILABLE,
                            ),
                        ),
                    ],
                ),
            ],
        )
    open_terminal = Button(
        "Open terminal",
        key="command-recovery-open-terminal",
        height=48,
        disabled=host not in granted,
        on_click=lambda e: go(f"/agents/{scope_key(host, agent_id)}"),
    )
    if journal.can_dismiss(row):
        return ft.Column(
            key=key,
            controls=[
                ft.Text(words.cannot_confirm_title(agent), weight=ft.FontWeight.BOLD),
                ft.Text(words.cannot_confirm_body(agent)),
                ft.Row(
                    wrap=True,
                    controls=[
                        open_terminal,
                        Button(
                            "Dismiss",
                            key="command-recovery-dismiss",
                            height=48,
                            on_click=lambda e: set_confirming((host, command_id)),
                        ),
                    ],
                ),
            ],
        )
    if row["outcome"] in FINAL:
        return ft.Column(
            key=key,
            controls=[
                ft.Text(words.not_sent(agent)),
                ft.Row(
                    wrap=True,
                    controls=[
                        Button(
                            "Reviewed",
                            height=48,
                            on_click=lambda e: run(journal.acknowledge(host, command_id)),
                        )
                    ],
                ),
            ],
        )
    return ft.Column(
        key=key,
        controls=[
            ft.Text(words.checking(agent)),
            ft.Row(
                wrap=True,
                controls=[
                    Button(
                        "Check now",
                        key="command-recovery-item-check",
                        height=48,
                        on_click=lambda e: run(journal.reconcile(source.command_status, granted)),
                    )
                ],
            ),
        ],
    )
