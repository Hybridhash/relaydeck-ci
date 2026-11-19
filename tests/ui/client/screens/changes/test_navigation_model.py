"""Random sequences of opening, buttons, browser Back/Forward and reloads over a push-only history.

The model is the browser: a list of entries, one of which is current; the app can only push. After
every browser Back the screen must show exactly one level above the one it showed (the Console from
the Graph); no route names a commit; and a reload on a commit route opens the Graph, not Working.
Many sequences run against the route hook with a stand-in screen; fewer run against the real screen.
"""

from __future__ import annotations

import asyncio
import random
import re
from collections import Counter
from typing import Any

import flet as ft

from relaydeck.client.models.routing import format_route
from relaydeck.client.screens.changes.parts.commit_route import use_commit_route
from tests.support.host_harness import HostApp, render_component, run
from tests.ui.git.gateway import KEY, Gateway, pager
from tests.ui.git.harness import Route, click, files, present, row_key

STAND_IN_SEQUENCES = 500
REAL_SEQUENCES = 8
STEPS = 12
SEED = 20260101
LEVELS = {"console": -1, "working": 0, "graph": 0, "commit": 1, "file": 2}
OID = re.compile(r"[0-9a-f]{12,}")
ACTIONS = ("commit", "file", "button", "back", "forward", "reload", "enter")
INVALID_SHARE = 0.15  # steps that pick any action, enabled or not


@ft.component
def Level(section: str, step: int, navigate: Any, leave: Any, control: dict[str, Any]) -> Any:
    """The route hook with a screen reduced to its level, wired as the Changes screen wires it."""
    level, set_level = ft.use_state("changes")

    def step_to(target: str) -> None:
        if target:
            set_level(target)
        else:
            leave()

    use_commit_route(
        level=level,
        section=section,
        step=step,
        restoring=False,
        on_level=lambda to, record, at: navigate(section=to, step=at, record=record),
        step_to=step_to,
    )
    control["set_level"] = set_level
    control["level"] = level
    return ft.Text(level, key="level")


@ft.component
def StandIn(control: dict[str, Any]) -> Any:
    """A route state and a browser history around `Level`, like `Route` without the screen."""
    first = ("agents", KEY, "changes", 0)
    route, set_route = ft.use_state(first)
    opened, set_opened = ft.use_state(0)  # a reload mounts the screen afresh
    history: Any = ft.use_ref(None)
    ahead: Any = ft.use_ref(None)
    if history.current is None:
        history.current = [("agents", "", "", 0), first]
        ahead.current = []

    def navigate(
        destination: str, selection: str = "", *, section: str = "", step: int = 0, record: bool
    ) -> None:
        entry = (destination, selection, section, step)
        if record:
            history.current.append(entry)
            ahead.current.clear()
        set_route(entry)

    def back() -> None:
        if len(history.current) > 1:
            ahead.current.append(history.current.pop())
        set_route(history.current[-1])

    def forward_again() -> None:
        if ahead.current:
            history.current.append(ahead.current.pop())
            set_route(history.current[-1])

    def reload() -> None:
        set_opened(lambda value: value + 1)

    def reset(start: str) -> None:
        entry = ("agents", KEY, start, 0)
        history.current[:] = [("agents", "", "", 0), entry]
        ahead.current.clear()
        set_route(entry)
        reload()

    def enter() -> None:
        navigate("agents", KEY, section="changes", step=0, record=True)

    control.update(
        enter=enter,
        history=history.current, ahead=ahead.current, back=back, forward=forward_again,
        reload=reload, reset=reset,
    )  # fmt: skip
    if not route[1]:
        return ft.Text("console", key="console")
    return Level(  # type: ignore[call-arg]
        route[2],
        route[3],
        lambda **kw: navigate("agents", KEY, **kw),
        lambda: navigate("agents", record=True),
        control,
        key=f"level-{opened}",
    )


async def quiet(app: HostApp) -> None:
    """Let the renders a state change asked for run; nothing here waits on a clock."""
    for _ in range(6):
        await asyncio.sleep(0)


class StandInDriver:
    """The hook over a screen that is only its level."""

    def __init__(self) -> None:
        self.app: HostApp | None = None
        self.control: dict[str, Any] = {}

    async def mount(self, start: str = "changes") -> tuple[HostApp, dict[str, Any]]:
        if self.app is None:
            self.app = await render_component(StandIn, self.control)
        self.control["reset"](start)
        await quiet(self.app)
        return self.app, self.control

    def shown(self, app: HostApp) -> str:
        if present(app, "console"):
            return "console"
        return {"changes": "graph", "commit": "commit", "commit-file": "file"}[
            str(app.find("level").value)
        ]

    async def act(
        self, app: HostApp, control: dict[str, Any], action: str, number: int
    ) -> tuple[HostApp, dict[str, Any]]:
        level = self.shown(app)
        if action == "commit" and level == "graph":
            control["set_level"]("commit")
        elif action == "file" and level == "commit":
            control["set_level"]("commit-file")
        elif action == "button" and level == "file":
            control["set_level"]("commit")
        elif action == "button" and level == "commit":
            control["set_level"]("changes")
        elif action in ("back", "forward"):
            control[action]()
        elif action == "reload":
            control["reload"]()
        elif action == "enter" and level == "console":
            control["enter"]()
        await quiet(app)
        return app, control


class RealDriver:
    """The same moves on the real Changes screen and its graph."""

    def __init__(self) -> None:
        self.gateway = Gateway(files())

    async def mount(
        self, start: str = "changes", trail: Any = None
    ) -> tuple[HostApp, dict[str, Any]]:
        control: dict[str, Any] = {}
        app = await render_component(
            Route, self.gateway, pager(), control, width=390, height=844, section=start, trail=trail
        )
        await app.settle(15)
        return app, control

    def shown(self, app: HostApp) -> str:
        if present(app, "console"):
            return "console"
        if present(app, "diff-rows"):
            return "file"
        if present(app, "commit-pane"):
            return "commit"
        if present(app, "changes-graph"):
            return "graph"
        if present(app, "changes-files"):
            return "working"
        raise AssertionError("no known screen is shown")

    async def act(
        self, app: HostApp, control: dict[str, Any], action: str, number: int
    ) -> tuple[HostApp, dict[str, Any]]:
        level = self.shown(app)
        if action == "commit" and level in ("working", "graph"):
            if level == "working":
                await click(app, "changes-view-graph")
                await app.wait_for(row_key(self.gateway, 0))
            await click(app, row_key(self.gateway, number))
            await app.wait_for("commit-file-0")
        elif action == "file" and level == "commit":
            await click(app, "commit-file-0")
            await app.wait_for("diff-rows")
        elif action == "button" and level == "file":
            await click(app, "changes-files-back")
        elif action == "button" and level == "commit":
            await click(app, "commit-back")
        elif action in ("back", "forward"):
            control["back" if action == "back" else "forward_again"]()
        elif action == "reload":
            app, control = await self.mount(
                trail=(list(control["history"]), list(control["ahead"]))
            )
        elif action == "enter" and level == "console":
            control["navigate"]("agents", KEY, section="changes", step=0)
        await app.settle(15)
        return app, control


def check_routes(control: dict[str, Any]) -> None:
    for entry in control["history"] + control["ahead"]:
        assert not OID.search(format_route(*entry)), entry
        assert entry[2] in ("", "changes", "file", "commit", "commit-file"), entry


def enabled(level: str, control: dict[str, Any]) -> list[str]:
    """The moves that do something at this level."""
    moves = ["reload"]
    if level in ("working", "graph"):
        moves.append("commit")
    if level == "commit":
        moves += ["file", "button"]
    if level == "file":
        moves.append("button")
    if level == "console":
        moves.append("enter")
    if len(control["history"]) > 1:
        moves.append("back")
    if control["ahead"]:
        moves += ["forward", "forward"]  # a Forward needs a Back first; make it likelier
    return moves


async def drain(driver: Any, app: HostApp, control: dict[str, Any]) -> HostApp:
    """Back, again and again: the history ends at its first entry, on the Console, within a bound.
    A Back at the Console may reopen Changes (the push-only trade-off) but only at its first level,
    and the Back after it leaves again."""
    limit = 2 * len(control["history"]) + 3
    for _ in range(limit):
        if len(control["history"]) == 1:
            break
        before = driver.shown(app)
        app, control = await driver.act(app, control, "back", 0)
        after = driver.shown(app)
        assert LEVELS[after] == max(LEVELS[before] - 1, -1) or (
            before == "console" and after in ("graph", "working")
        ), (before, after)
    assert len(control["history"]) == 1 and driver.shown(app) == "console"
    return app


async def explore(driver: Any, sequences: int, seed: int) -> Counter[str]:
    dice = random.Random(seed)  # noqa: S311 - a seeded sequence, not a secret
    covered: Counter[str] = Counter()
    for sequence in range(sequences):
        start = dice.choice(["changes", "commit", "commit-file"])  # a reload or a direct link
        app, control = await driver.mount(start)
        trace: list[str] = [f"start:{start}"]
        try:
            if start != "changes":
                assert driver.shown(app) == "graph", driver.shown(app)
            for _ in range(STEPS):
                before, entries = driver.shown(app), len(control["history"])
                moves = enabled(before, control)
                valid = dice.random() >= INVALID_SHARE
                action = dice.choice(moves if valid else ACTIONS)
                number = dice.randrange(2)
                covered[f"{action}@{before}" if action in moves else "invalid"] += 1
                trace.append(f"{action}:{number}")
                app, control = await driver.act(app, control, action, number)
                check_routes(control)
                after = driver.shown(app)
                if action == "back" and entries > 1 and before != "console":
                    assert LEVELS[after] == max(LEVELS[before] - 1, -1), (before, after)
                if action == "back" and before == "console":
                    # Reopening Changes is allowed once, at its first level only; at the very
                    # first entry nothing moves.
                    assert after in ("console", "graph", "working"), (before, after)
                    assert entries > 1 or after == "console"
                if action == "enter" and before == "console":
                    assert after in ("graph", "working"), after
                if action == "reload":
                    entry = control["history"][-1]
                    if entry[1] and entry[2] in ("commit", "commit-file"):
                        assert after in ("graph", "console") and after != "working", (entry, after)
            app = await drain(driver, app, control)
        except AssertionError as failure:
            raise AssertionError(f"sequence {sequence}: {trace}: {failure}") from failure
    return covered


# What the run must have exercised, as a share of the sequences: a model that mostly sat on the
# Console, or never opened a file, proves little.
COVERAGE = (
    "commit@graph",
    "file@commit",
    "button@file",
    "button@commit",
    "back@file",
    "back@commit",
    "back@graph",
    "back@console",
    "forward@graph",
    "forward@commit",
    "reload@graph",
    "reload@commit",
    "reload@file",
    "enter@console",
)


RARE = {"forward@commit": 0.02}  # a Forward at the commit's files needs Back from a file first


def check_coverage(covered: Counter[str], sequences: int, per_sequence: float) -> None:
    short = {
        name: covered[name]
        for name in COVERAGE
        if (per_sequence or name not in RARE)  # the short real run skips the rare ones
        and covered[name]
        < max(1, sequences * (RARE.get(name, per_sequence) if per_sequence else 0))
    }
    assert not short, (short, dict(covered))


def test_random_histories_on_the_hook_go_up_one_level_and_never_name_a_commit() -> None:
    covered = run(explore(StandInDriver(), STAND_IN_SEQUENCES, SEED))
    check_coverage(covered, STAND_IN_SEQUENCES, 0.1)


def test_random_histories_on_the_real_screen_go_up_one_level_and_open_the_graph_on_reload() -> None:
    covered = run(explore(RealDriver(), REAL_SEQUENCES, SEED + 1))
    check_coverage(covered, REAL_SEQUENCES, 0.0)  # at least once each; the hook run is the bulk
