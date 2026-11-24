"""Pure Changes selection, list, summary, commit and base state transitions."""

from __future__ import annotations

from dataclasses import replace

from relaydeck.client.features.changes.diff_window import anchor_for as anchor_for
from relaydeck.client.features.changes.diff_window import earlier_page as earlier_page
from relaydeck.client.features.changes.diff_window import load_diff as load_diff
from relaydeck.client.features.changes.diff_window import page_to_reload as page_to_reload
from relaydeck.client.features.changes.diff_window import receive_diff as receive_diff
from relaydeck.client.features.changes.diff_window import request_diff as request_diff
from relaydeck.client.features.changes.diff_window import resident_pages as resident_pages
from relaydeck.client.features.changes.diff_window import resident_rows as resident_rows
from relaydeck.client.features.changes.diff_window import select_file as select_file
from relaydeck.client.features.changes.diff_window import set_anchor as set_anchor
from relaydeck.client.features.changes.diff_window import step_file as step_file
from relaydeck.client.features.changes.graph_model import receive_graph as receive_graph
from relaydeck.client.features.changes.graph_model import request_graph as request_graph
from relaydeck.client.features.changes.graph_model import set_graph_mode as set_graph_mode
from relaydeck.client.features.changes.graph_state import GraphState
from relaydeck.client.features.changes.loads import _current, _ready, _renewed, _start, _with_load
from relaydeck.client.features.changes.state import MAX_ANCHORS as MAX_ANCHORS
from relaydeck.client.features.changes.state import MAX_RESELECT_FILES as MAX_RESELECT_FILES
from relaydeck.client.features.changes.state import MAX_RESIDENT_ROWS as MAX_RESIDENT_ROWS
from relaydeck.client.features.changes.state import Anchor as Anchor
from relaydeck.client.features.changes.state import ChangesModel as ChangesModel
from relaydeck.client.features.changes.state import ChangesPage as ChangesPage
from relaydeck.client.features.changes.state import CommitKey as CommitKey
from relaydeck.client.features.changes.state import CommitOpening as CommitOpening
from relaydeck.client.features.changes.state import CommitReading as CommitReading
from relaydeck.client.features.changes.state import Condition as Condition
from relaydeck.client.features.changes.state import DiffWindow as DiffWindow
from relaydeck.client.features.changes.state import FailedPoll as FailedPoll
from relaydeck.client.features.changes.state import Load as Load
from relaydeck.client.features.changes.state import LoadStatus as LoadStatus
from relaydeck.client.features.changes.state import Request as Request
from relaydeck.client.features.changes.state import Scope as Scope
from relaydeck.client.features.changes.state import Slot as Slot
from relaydeck.client.features.changes.state import View as View
from relaydeck.contracts.git import BaseCandidate, FileEntry, WorktreeSummary

# Typed reasons and envelope codes that change what a screen can show at all.
_CONDITION_FOR_REASON: dict[str, Condition] = {
    "unsupported_config": "unsupported_config",
    "git_unavailable": "git_unavailable",
    "no_base": "no_base",
    "host_offline": "host_offline",
    "unsupported_capability": "not_permitted",
    "forbidden": "not_permitted",
    "unauthenticated": "not_permitted",
    "not_found": "folder_gone",
}
# The snapshot or cursor the reader holds is gone; a fresh load is available.
_STALE_REASONS = frozenset({"stale_snapshot", "stale_epoch", "stale_revision", "expired"})
# The Mac is busy or limiting requests: the list is asked for again, so this is not a state.
_TRANSIENT_REASONS = frozenset({"busy", "rate_limited"})


def select(
    model: ChangesModel,
    agent_id: str,
    worktree_id: str | None = None,
    scope: Scope = "working",
) -> ChangesModel:
    """Show another agent (or worktree): nothing from the previous one survives."""
    return _renewed(model, ChangesModel(agent_id=agent_id, worktree_id=worktree_id, scope=scope))


def set_scope(model: ChangesModel, scope: Scope) -> ChangesModel:
    if scope == model.scope:
        return model
    fresh = ChangesModel(
        agent_id=model.agent_id,
        worktree_id=model.worktree_id,
        scope=scope,
        condition="calculating" if model.condition == "ok" else model.condition,
        branch=model.branch,
        detached=model.detached,
        short_oid=model.short_oid,
        base_name=model.base_name,
        base_revision=model.base_revision,
        shared=model.shared,
        working_seen=model.working_seen,
        branch_seen=model.branch_seen,
    )
    return _renewed(model, fresh)


def begin_refresh(model: ChangesModel) -> ChangesModel:
    """Apply the newer revision: reload the list and diff, keeping agent, scope and chosen file."""
    cleared = replace(
        model,
        condition="calculating" if model.condition == "ok" else model.condition,
        snapshot=None,
        revision_baseline=None,
        updated_available=False,
        files=(),
        files_next=None,
        diff=None,
        selected_hunk=None,
        head=None,
        comparison_ready=False,
        anchors=(),
        additions=None,  # totals belong to the snapshot being dropped
        deletions=None,
        graph=GraphState(mode=model.graph.mode),
        commit=None,
        commit_lost=False,
    )
    return _renewed(model, cleared)


def apply_summary(model: ChangesModel, summary: WorktreeSummary) -> ChangesModel:
    """Fold in the agent's card summary: its state, and its revisions for "Updated".

    A list on screen keeps what it was loaded with (branch line, base, time, partial and shared
    marks); a newer summary can only raise "Updated" until the reader refreshes.
    """
    if summary.agent_id != model.agent_id:
        return model
    if isinstance(summary, FailedPoll):
        return _poll_failed(model, summary)
    worktree = summary.worktree_id or None  # no repository, no worktree
    if model.worktree_id is not None and worktree != model.worktree_id:
        model = select(model, summary.agent_id, worktree, model.scope)
    shown = model.snapshot is not None
    condition: Condition = summary.state
    if condition == "ok" and model.scope == "branch" and summary.base_name is None:
        condition = model.condition if shown else "no_base"
    elif condition == "calculating" and shown:
        condition = model.condition  # a refresh of data already on screen is not a new unknown
    updated = replace(model, worktree_id=worktree, condition=condition)
    if not shown:
        updated = replace(
            updated,
            branch=summary.branch,
            detached=summary.detached,
            short_oid=summary.short_oid,
            base_name=summary.base_name,
            shared=summary.shared,
            partial=summary.partial,
            as_of_ms=summary.as_of_ms,
        )
    if summary.state != "ok":
        return updated  # a state the server reports carries no revisions
    return _note(updated, summary.revision or None, summary.branch_scope.revision)


def _poll_failed(model: ChangesModel, summary: WorktreeSummary) -> ChangesModel:
    """The stand-in for a summary that could not be fetched. Data on screen is kept; a screen
    with nothing yet says unavailable instead of calculating."""
    if model.snapshot is not None or model.worktree_id not in (None, summary.worktree_id or None):
        return model
    return replace(model, condition="unavailable")


def note_revision(model: ChangesModel, revision: str | None) -> ChangesModel:
    """Record the newest summary revision for the scope on screen (see `_note`)."""
    if model.scope == "branch":
        return _note(model, model.working_seen, revision)
    return _note(model, revision, model.branch_seen)


def _note(model: ChangesModel, working: str | None, branch: str | None) -> ChangesModel:
    """Record the newest summary revisions. It only raises a flag; the reader's data is
    untouched.
    """
    seen = replace(model, working_seen=working, branch_seen=branch)
    revision = seen.revision_seen
    if model.snapshot is None:
        return seen
    if model.revision_baseline is None:
        return replace(seen, revision_baseline=revision)
    return replace(
        seen, updated_available=model.updated_available or revision != model.revision_baseline
    )


def is_clean(model: ChangesModel) -> bool:
    """True only for a loaded, healthy list with no files: calculating is never clean."""
    return model.condition == "ok" and model.files_load.status == "ready" and not model.files


def request_files(
    model: ChangesModel, *, more: bool = False
) -> tuple[ChangesModel, Request | None]:
    """Start the first page (replacing any request in flight) or the next one."""
    if model.agent_id is None:
        return model, None
    if more:
        if model.files_next is None or model.files_load.status == "loading":
            return model, None
        started, token = _start(model, "files")
        return started, Request("files", token, cursor=model.files_next)
    started, token = _start(model, "files")
    return started, Request("files", token)


def receive_files(
    model: ChangesModel, token: int, page: ChangesPage, *, more: bool = False
) -> ChangesModel:
    if not _current(model, "files", token):
        return model
    if more:
        if page.snapshot != model.snapshot:
            return replace(_ready(model, "files"), updated_available=True)
        files = model.files + page.files
        appended = replace(
            _ready(model, "files"),
            files=files,
            files_next=page.next,
            partial=model.partial or page.partial,
        )
        return _found(appended, page.files, page.next, len(files))
    selected = _still_selected(model, page)
    first = replace(
        _ready(model, "files"),
        condition="ok",
        snapshot=page.snapshot,
        revision_baseline=model.revision_seen,
        updated_available=False,
        files=page.files,
        files_next=page.next,
        selected_file=selected.file_id if selected is not None else None,
        selected_path=model.selected_path,
        diff=model.diff if model.diff and model.diff.snapshot == page.snapshot else None,
        head=page.head,
        comparison_ready=page.authoritative or page.head is not None,
        selected_hunk=model.selected_hunk if model.snapshot == page.snapshot else None,
        branch=page.branch if page.authoritative else page.branch or model.branch,
        detached=page.detached if page.detached is not None else model.detached,
        short_oid=page.short_oid if page.authoritative else page.short_oid or model.short_oid,
        base_name=page.base_name if page.authoritative else page.base_name or model.base_name,
        base_revision=page.base_revision
        if page.authoritative
        else page.base_revision or model.base_revision,
        additions=page.additions if page.additions is not None else model.additions,
        deletions=page.deletions if page.deletions is not None else model.deletions,
        as_of_ms=page.as_of_ms,
        partial=page.partial,
    )
    if selected is not None:
        first = replace(first, selected_path=selected.path)
    return _found(first, page.files, page.next, len(page.files))


def reselecting(model: ChangesModel) -> bool:
    """The reader's file is not in the pages loaded so far; later pages are still being searched."""
    return model.selected_file is None and model.selected_path is not None


def _found(
    model: ChangesModel, arrived: tuple[FileEntry, ...], more: str | None, loaded: int
) -> ChangesModel:
    """Look for a pending selection in the pages that just arrived; give up at the end of the
    list or after `MAX_RESELECT_FILES` files."""
    if not reselecting(model):
        return model
    match = next((f for f in arrived if f.path == model.selected_path), None)
    if match is not None:
        return replace(model, selected_file=match.file_id)
    if more is None or loaded >= MAX_RESELECT_FILES:
        return replace(model, selected_path=None)
    return model


def _still_selected(model: ChangesModel, page: ChangesPage) -> FileEntry | None:
    """The reader's file on the new first page: the same id, else the same path (ids belong to
    one snapshot, so a refresh renames every file)."""
    if model.selected_file is not None:
        same_id = next((f for f in page.files if f.file_id == model.selected_file), None)
        if same_id is not None:
            return same_id
    if model.selected_path is None:
        return None
    return next((f for f in page.files if f.path == model.selected_path), None)


def fail(model: ChangesModel, slot: Slot, token: int, reason: str) -> ChangesModel:
    """A failed request. `reason` is the typed reason, or the envelope code when there is none."""
    if not _current(model, slot, token):
        return model
    failed = _with_load(model, slot, Load(token, "error", reason))
    if slot == "files" and reselecting(failed):
        failed = replace(failed, selected_path=None)  # the search for the reader's file ends
    if reason in _STALE_REASONS:
        failed = replace(failed, updated_available=True)
    condition = _CONDITION_FOR_REASON.get(reason)
    if slot == "files" and condition is None and model.snapshot is None:
        if reason not in _STALE_REASONS | _TRANSIENT_REASONS:
            condition = "unavailable"  # a first list that failed is a state, never "Calculating"
    if slot == "files" and condition is not None:
        failed = replace(failed, condition=condition)
    return failed


def set_view(model: ChangesModel, view: View) -> tuple[ChangesModel, Request | None]:
    if view == "graph" and model.scope != "branch":
        return model, None
    shown = replace(model, view=view)
    if view != model.view:
        shown = replace(
            shown,
            graph_check=Load(model.graph_check.token + 1),
            commit=None,
            commit_lost=False,
        )
    if view == "graph" and shown.graph.page is None:
        return request_graph(shown)
    return shown, None


def open_base_picker(model: ChangesModel) -> tuple[ChangesModel, Request | None]:
    opened = replace(model, picker_open=True)
    if model.bases or model.bases_load.status == "loading":
        return opened, None
    return request_bases(opened)


def close_base_picker(model: ChangesModel) -> ChangesModel:
    return replace(model, picker_open=False)


def request_bases(
    model: ChangesModel, *, more: bool = False
) -> tuple[ChangesModel, Request | None]:
    if more and (model.bases_next is None or model.bases_load.status == "loading"):
        return model, None
    started, token = _start(model, "bases")
    return started, Request("bases", token, cursor=model.bases_next if more else None)


def receive_bases(
    model: ChangesModel,
    token: int,
    candidates: tuple[BaseCandidate, ...],
    next_cursor: str | None,
    *,
    more: bool = False,
) -> ChangesModel:
    if not _current(model, "bases", token):
        return model
    return replace(
        _ready(model, "bases"),
        bases=model.bases + candidates if more else candidates,
        bases_next=next_cursor,
    )


def request_save_base(model: ChangesModel, base_id: str) -> tuple[ChangesModel, Request]:
    started, token = _start(model, "save")
    return started, Request("save", token, base_id=base_id)


def receive_base_saved(
    model: ChangesModel, token: int, base_name: str, base_revision: str | None
) -> ChangesModel:
    """The new base replaces the old one; a Branch list built on the old base is dropped."""
    if not _current(model, "save", token):
        return model
    saved = replace(
        _ready(model, "save"),
        base_name=base_name,
        base_revision=base_revision,
        picker_open=False,
        condition="calculating" if model.condition == "no_base" else model.condition,
    )
    return begin_refresh(saved) if model.scope == "branch" else saved
