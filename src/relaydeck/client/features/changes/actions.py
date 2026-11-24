"""What the Changes screen can ask of its model."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import relaydeck.client.features.changes.model as m
from relaydeck.client.features.changes.graph_state import GraphAnchor
from relaydeck.contracts.git import GraphMode


class ReadingActions(Protocol):
    """What reading a file asks of its owner: the diff, the file pager and picker, the copy sheet
    and a question. The list of changes and an open commit each provide it."""

    @property
    def refresh(self) -> Callable[[], None]: ...
    @property
    def select_file(self) -> Callable[[str | None], None]: ...
    @property
    def step_file(self) -> Callable[[int], None]: ...
    @property
    def more_files(self) -> Callable[[], None]: ...
    @property
    def more_diff(self) -> Callable[[], None]: ...
    @property
    def earlier_diff(self) -> Callable[[], None]: ...
    @property
    def load_diff(self) -> Callable[[], None]: ...
    @property
    def remember(self) -> Callable[[m.Anchor], None]: ...
    @property
    def current(self) -> Callable[[], m.ChangesModel]: ...
    @property
    def select_hunk(self) -> Callable[[tuple[int, int]], None]: ...


@dataclass(frozen=True, slots=True)
class ChangesActions:
    refresh: Callable[[], None]
    set_scope: Callable[[m.Scope], None]
    set_view: Callable[[m.View], None]
    select_file: Callable[[str | None], None]
    step_file: Callable[[int], None]
    more_files: Callable[[], None]
    more_diff: Callable[[], None]
    earlier_diff: Callable[[], None]
    more_graph: Callable[[], None]
    load_diff: Callable[[], None]
    open_bases: Callable[[], None]
    close_bases: Callable[[], None]
    more_bases: Callable[[], None]
    save_base: Callable[[str], None]
    remember: Callable[[m.Anchor], None]
    current: Callable[[], m.ChangesModel]
    select_hunk: Callable[[tuple[int, int]], None]
    graph_mode: Callable[[GraphMode], None]
    graph_anchor: Callable[[GraphMode, str, GraphAnchor], None]
    open_commit: Callable[[GraphMode, str, str], None]
    close_commit: Callable[[], None]
    commit: ReadingActions | None = None  # the reading actions, acting on the open commit
