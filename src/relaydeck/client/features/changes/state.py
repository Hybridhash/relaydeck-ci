"""Immutable values shared by the Changes state transitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from relaydeck.client.features.changes.graph_state import GraphState
from relaydeck.contracts.git import (
    BaseCandidate,
    FileDiff,
    FileEntry,
    GraphCommit,
    GraphMode,
    RenderState,
    WorktreeSummary,
)

Scope = Literal["working", "branch", "commit"]
View = Literal["files", "graph"]
Condition = Literal[
    "calculating",
    "ok",
    "no_repo",
    "folder_gone",
    "host_offline",
    "not_permitted",
    "git_unavailable",
    "unsupported_config",
    "unavailable",
    "no_base",
]
LoadStatus = Literal["idle", "loading", "ready", "error"]
Slot = Literal["files", "diff", "bases", "save", "graph", "graph_check"]

MAX_RESIDENT_ROWS = 500
MAX_ANCHORS = 200
MAX_RESELECT_FILES = 5000  # how far into a long list a refresh looks for the reader's file


@dataclass(frozen=True, slots=True, kw_only=True)
class FailedPoll(WorktreeSummary):
    """What a card and the screen are given when the summary could not be fetched at all.

    Only a failed transport poll makes one; a state the server reports (no repository, folder
    gone, unsupported configuration, git unavailable, unavailable) is an ordinary summary, even
    though it has no revision.
    """


@dataclass(frozen=True, slots=True)
class Load:
    """Progress of one kind of request. `token` only ever grows."""

    token: int = 0
    status: LoadStatus = "idle"
    error: str | None = None


@dataclass(frozen=True, slots=True)
class Request:
    """What the caller must fetch; it echoes `token` back with the reply."""

    slot: Slot
    token: int
    cursor: str | None = None
    page: int | None = None
    base_id: str | None = None
    load: bool = False  # the reader chose "Load diff" for this file
    mode: GraphMode | None = None
    check: bool = False
    commit: CommitOpening | None = None  # the request belongs to the open commit, not the list


@dataclass(frozen=True, slots=True)
class Anchor:
    """A row of a file, independent of which pages are resident. `page` is the page index in the
    file's sequence, `hunk` the hunk's position on that page, `fragment` that hunk's fragment
    number and `row` the index among its lines (-1 for the hunk's header).
    """

    file_id: str
    page: int
    hunk: int
    fragment: int
    row: int
    cursors: tuple[str | None, ...] = ()


@dataclass(frozen=True, slots=True)
class ChangesPage:
    """One reply of the file list, as the repository adapter hands it to the model."""

    snapshot: str
    files: tuple[FileEntry, ...]
    next: str | None = None
    partial: bool = False
    branch: str | None = None
    detached: bool | None = None
    head: str | None = None
    short_oid: str | None = None
    base_name: str | None = None
    base_revision: str | None = None
    additions: int | None = None
    deletions: int | None = None
    as_of_ms: int | None = None
    authoritative: bool = False


@dataclass(frozen=True, slots=True)
class DiffWindow:
    """The resident pages of one file's diff. `cursors[k]` fetches page `k` (known for every
    page up to the one after the last received), so an evicted page can be fetched again.
    """

    file_id: str
    snapshot: str
    pages: tuple[tuple[int, FileDiff], ...] = ()
    cursors: tuple[str | None, ...] = (None,)
    end: int | None = None
    render: RenderState | None = None
    loaded: bool = False

    @property
    def last_page(self) -> int:
        """The last resident page, or -1 when none is held."""
        return self.pages[-1][0] if self.pages else -1

    @property
    def complete(self) -> bool:
        """Nothing follows the last resident page."""
        return self.end is not None and self.last_page == self.end


@dataclass(frozen=True, slots=True, repr=False)
class CommitKey:
    """Which commit of which graph a reading belongs to; replies for any other are dropped."""

    mode: GraphMode
    revision: str
    oid: str


@dataclass(frozen=True, slots=True, repr=False)
class CommitOpening:
    """One opening of a commit. `generation` is shared by no other opening, so a reply to an
    earlier opening of the same commit cannot be mistaken for a reply to this one."""

    key: CommitKey
    generation: int


@dataclass(frozen=True, slots=True, repr=False)
class CommitReading:
    """An open commit: the graph row it came from and its own file list and diff."""

    key: CommitKey
    commit: GraphCommit
    model: ChangesModel
    generation: int

    @property
    def opening(self) -> CommitOpening:
        return CommitOpening(self.key, self.generation)


@dataclass(frozen=True, slots=True)
class ChangesModel:
    agent_id: str | None = None
    worktree_id: str | None = None
    scope: Scope = "working"
    condition: Condition = "calculating"
    branch: str | None = None
    detached: bool = False
    head: str | None = None
    comparison_ready: bool = False
    short_oid: str | None = None
    base_name: str | None = None
    base_revision: str | None = None
    additions: int | None = None
    deletions: int | None = None
    as_of_ms: int | None = None
    partial: bool = False
    shared: bool = False
    snapshot: str | None = None
    working_seen: str | None = None  # the newest summary's Working revision
    branch_seen: str | None = None  # the newest summary's Branch revision (None: no base)
    revision_baseline: str | None = None  # the summary revision when the list was loaded
    updated_available: bool = False
    files: tuple[FileEntry, ...] = ()
    files_next: str | None = None
    selected_file: str | None = None
    selected_hunk: tuple[int, int] | None = None
    reading_notice: str = ""
    selected_path: str | None = None  # finds the same file again in a newer snapshot
    diff: DiffWindow | None = None
    anchors: tuple[Anchor, ...] = ()
    bases: tuple[BaseCandidate, ...] = ()
    bases_next: str | None = None
    picker_open: bool = False
    files_load: Load = Load()
    diff_load: Load = Load()
    bases_load: Load = Load()
    save_load: Load = Load()
    view: View = "files"
    graph: GraphState = GraphState()
    graph_load: Load = Load()
    commit: CommitReading | None = None
    commit_lost: bool = False  # a commit the reader had open is not on the refreshed first page
    graph_check: Load = Load()

    @property
    def revision_seen(self) -> str | None:
        """The newest summary revision for the scope on screen."""
        return self.branch_seen if self.scope == "branch" else self.working_seen
