"""Shared pieces for the Changes route tests: the app root's part, and small driving helpers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast

import flet as ft

from relaydeck.client.app.workflows.changes import use_changes_wiring
from relaydeck.client.features.changes.repository import ChangesRepository
from relaydeck.client.models.input.key_bar import width_class
from relaydeck.client.screens.changes.screen import ChangesScreen
from tests.support.git_patches import added_patch, two_hunk_patch
from tests.support.host_harness import HostApp, render_component
from tests.support.ui.navigation import _StubTransport
from tests.ui.git.gateway import AGENT, HOST, KEY, WORKTREE, FileSpec, Gateway, pager


@ft.component
def Route(
    gateway: Gateway,
    rows: Any,
    control: dict[str, Any],
    wide: bool = False,
    connected: bool | None = None,
    width: int = 390,
    height: int = 844,
    section: str = "changes",
    step: int = 0,
    trail: Any = None,
) -> ft.Control:
    """What the app's root does with the wiring: a route state, a navigate function, the screen.

    `control["history"]` is the browser's history (the Console, then the entries navigate recorded);
    `control["back"]()` is the browser's Back: the route follows the previous entry without the
    screen having navigated. `trail` is a (history, ahead) pair a reload keeps."""
    route, set_route = ft.use_state(
        trail[0][-1] if trail is not None else ("agents", KEY, section, step)
    )
    history: Any = ft.use_ref(None)
    ahead: Any = ft.use_ref(None)  # entries Back stepped over, for Forward
    if history.current is None:
        if trail is not None:
            history.current, ahead.current = list(trail[0]), list(trail[1])
        else:
            history.current = [("agents", "", "", 0), ("agents", KEY, section, step)]
            ahead.current = []
    account, set_account = ft.use_state("account-a")
    is_wide, set_wide = ft.use_state(wide)
    link, set_link = ft.use_state(connected)
    viewport, set_viewport = ft.use_state((width, height))

    def navigate(
        destination: str,
        selection: str = "",
        *,
        section: str = "",
        step: int = 0,
        record: bool = True,
    ) -> None:
        if record:
            history.current.append((destination, selection, section, step))
            ahead.current.clear()  # a new entry drops what was ahead, as a browser does
        set_route((destination, selection, section, step))

    def back() -> None:
        if len(history.current) > 1:
            ahead.current.append(history.current.pop())
        set_route(history.current[-1])

    def forward(destination: str, selection: str = "", section: str = "", step: int = 0) -> None:
        """A Forward step to an entry that was not stepped back over."""
        history.current.append((destination, selection, section, step))
        set_route((destination, selection, section, step))

    def forward_again() -> None:
        """The browser's Forward over the entry Back stepped over last."""
        if ahead.current:
            history.current.append(ahead.current.pop())
            set_route(history.current[-1])

    control.update(
        history=history.current,
        ahead=ahead.current,
        route=lambda: route,
        back=back,
        forward=forward,
        forward_again=forward_again,
        navigate=navigate,
        set_account=set_account,
        set_wide=set_wide,
        set_connected=set_link,
        set_viewport=set_viewport,
    )
    changes = use_changes_wiring(
        transport=gateway,
        pager=rows,
        destination=route[0],
        selection=route[1],
        section=route[2],
        step=route[3],
        foreground=True,
        wide=is_wide,
        phone=width_class(viewport[0]) == "compact",
        navigate=navigate,
        account=account,
        connected=link,
    )
    if changes.screen is None:
        return ft.Text("console", key="console")
    return cast(ft.Control, changes.screen)


def small_files() -> list[FileSpec]:
    return [
        FileSpec("pkg/a.py", two_hunk_patch("pkg/a.py")),
        FileSpec("pkg/b.py", two_hunk_patch("pkg/b.py")),
    ]


async def mount(gateway: Gateway, **kwargs: Any) -> tuple[HostApp, dict[str, Any]]:
    control: dict[str, Any] = {}
    app = await render_component(Route, gateway, pager(), control, **kwargs)
    return app, control


async def click(app: HostApp, key: str) -> None:
    await app.set_props_and_fire(key, {}, "click")


async def tick(app: HostApp, key: str, value: bool) -> None:
    await app.set_props_and_fire(key, {"value": value}, "change", str(value).lower())


async def until(condition: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "condition not reached"
        await asyncio.sleep(0.01)


def present(app: HostApp, key: str) -> bool:
    try:
        app.find(key)
    except KeyError:
        return False
    return True


def scope_is(app: HostApp, scope: str) -> bool:
    return bool(app.find(f"changes-scope-{scope}").data["selected"])


def message_text(app: HostApp) -> str:
    return str(app.find("changes-state").content.controls[0].value)


def files() -> list[FileSpec]:
    return [
        FileSpec("pkg/a.py", two_hunk_patch("pkg/a.py")),
        FileSpec("pkg/big.py", added_patch("pkg/big.py", 600), status="A"),
    ]


async def render(
    gateway: Gateway,
    *,
    wide: bool = False,
    copied: list[str] | None = None,
    on_ask: Any = None,
    ask_status: str = "idle",
    bookmark: Any = None,
    width: int | None = None,
    height: int | None = None,
    reviews: Any = None,
    checked: Any = None,
) -> Any:
    width = width if width is not None else 1376 if wide else 390
    height = height if height is not None else 1000 if wide else 844
    app = await render_component(
        ChangesScreen,
        HOST,
        AGENT,
        WORKTREE,
        "Synthetic agent",
        ChangesRepository(gateway),  # type: ignore[arg-type]
        summary=gateway.summary(),
        wide=wide,
        phone=width_class(width) == "compact",
        on_ask=on_ask,
        ask_status=ask_status,
        bookmark=bookmark,
        reviews=reviews,
        checked=checked,
        on_back=lambda: None,
        on_terminal=lambda: None,
        on_copy=(copied.append if copied is not None else None),
    )
    app.session.apply_patch(app.page._i, {"width": width, "height": height})
    await app.settle()
    return app


def row_key(gateway: Gateway, number: int) -> str:
    return f"graph-row-{gateway.snapshot('branch')}:graph{gateway.graph_revision}-{number:040x}"


async def graph(gateway: Gateway, **kwargs: Any) -> Any:
    app = await render(gateway, **kwargs)
    await app.wait_for("changes-files")
    await click(app, "changes-view-graph")
    await app.wait_for(row_key(gateway, 0))
    return app


async def press(app: Any, key: str) -> None:
    event = {"key": key, "ctrl": False, "meta": False, "alt": False, "shift": False}
    await app.session.dispatch_event(app.page._i, "keyboard_event", event)
    await app.settle()


class Transport(_StubTransport):
    def __init__(self) -> None:
        super().__init__()
        self.git = Gateway(files())

    async def git_summaries(self, host: str, agents: list[str]) -> dict[str, Any]:
        return await self.git.git_summaries(host, agents)

    async def git_changes(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self.git.git_changes(*args, **kwargs)

    async def git_file_diff(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self.git.git_file_diff(*args, **kwargs)

    async def git_graph(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self.git.git_graph(*args, **kwargs)

    async def git_commit_changes(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self.git.git_commit_changes(*args, **kwargs)


def summary_text(control: Any) -> str:
    """What a graph summary line reads, whether it is one string or coloured spans."""
    spans = getattr(control, "spans", None)
    return "".join(span.text or "" for span in spans) if spans else str(control.value)


def built_rows(app: Any, name: str = "changes-files") -> list[Any]:
    """The rows a windowed list has built (not its spacers)."""
    return [
        c
        for c in app.find(name).controls
        if not str(getattr(c, "key", "")).endswith(("-before", "-after"))
    ]


def scroll_to(app: Any, name: str, pixels: float) -> None:
    """Scroll a windowed list the way the client reports it."""
    app.find(name).on_scroll(SimpleNamespace(pixels=pixels, viewport_dimension=800.0))


async def scroll_until_built(app: Any, name: str, key: str, row: float = 24.0) -> None:
    """Scroll a windowed list from the top, a screen at a time, until the row `key` is built.

    The list always gets at least one scroll event, as a reader's first touch would give."""
    rows = int(app.find(name).data["rows"])
    for step in range(0, int(rows * row) + 800, 700):
        scroll_to(app, name, float(step))
        await app.settle(2)
        if present(app, key):
            return
    assert present(app, key), f"{key} never came into the window"
