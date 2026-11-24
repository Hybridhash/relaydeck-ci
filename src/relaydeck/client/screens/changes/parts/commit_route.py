"""An open commit, and a file that takes the whole phone screen, take part in browser history: a
screen level per route section. Back from a file or a commit (or from one of its files) returns
one level, the way the pushed screens do on a phone.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import flet as ft

from relaydeck.client.features.changes.api import ChangesModel

LEVELS = ("changes", "commit", "commit-file")
FILE = "file"  # a file of the list that takes the whole screen: one level below the list
DEPTH = {"changes": 0, FILE: 1, "commit": 1, "commit-file": 2}


def rank(section: str) -> int:
    return DEPTH.get(section, 0)


def level_of(model: ChangesModel, pushed: bool, file_pushed: bool = False) -> str:
    """The screen level the model is at; a commit beside the graph, or a file beside the list, is
    not a level of its own."""
    reading = model.commit
    if reading is not None and pushed:
        return LEVELS[2] if reading.model.selected_file is not None else LEVELS[1]
    return FILE if file_pushed else LEVELS[0]


def use_commit_route(
    *,
    level: str,
    section: str,
    step: int,
    restoring: bool,
    on_level: Callable[[str, bool, int], None] | None,
    step_to: Callable[[str], None],
) -> None:
    """Keep the route section and the screen level together. Each history entry the app pushes
    carries a number (`step`), a counter that names nothing else, and the hook remembers the
    number of the entry it is on.
    """
    seen: Any = ft.use_ref(None)
    at: Any = ft.use_ref(step)  # the entry the browser is on
    issued: Any = ft.use_ref(step)  # the newest number given out

    def settle(target: str, push: bool) -> None:
        if push:
            issued.current += 1
            at.current = issued.current
        if on_level is not None:
            on_level(target, push, at.current)

    def sync() -> None:
        previous = seen.current
        seen.current = (level, section, step)
        if on_level is None:
            return
        if previous is None:
            at.current = issued.current = step
            if rank(section) > rank(level) and not restoring:
                settle(level, False)
            elif rank(level) > rank(section):
                settle(level, True)  # opened already deeper than the route: Back must return
            return
        if level != previous[0]:
            if rank(level) > rank(previous[0]) and rank(section) < rank(level):
                settle(level, True)  # deeper than any entry in the history: push one
        elif (section, step) != (previous[1], previous[2]) and step != at.current:
            moved = step - at.current
            at.current = step
            issued.current = max(issued.current, step)
            if moved < 0:  # the browser went back
                target = rank(section) if rank(section) < rank(level) else rank(level) - 1
                step_to(LEVELS[target] if target >= 0 else "")
                if target >= 0 and section != LEVELS[target]:
                    # The route still names the deeper section the entry was made for; what is
                    # shown now is the level, so the next descent pushes its own numbered entry.
                    settle(LEVELS[target], False)
            else:  # forward: nothing is in memory to go to
                settle(level, False)

    ft.use_effect(sync, dependencies=[level, section, step])
