"""An open commit is read through its own file list; only its own replies reach it."""

from __future__ import annotations

from dataclasses import replace

import pytest

import relaydeck.client.features.changes.model as m
from relaydeck.client.features.ask_agent.target import target, validate
from relaydeck.client.features.changes.commit_model import (
    close_commit,
    commit_receive,
    commit_step,
    open_commit,
    resume,
)
from relaydeck.client.features.changes.reading_bookmark import ReadingBookmark
from relaydeck.client.features.changes.state import CommitKey
from tests.support.changes_model import loaded
from tests.support.commit_graph import REVISION, arrive, fresh, graph_model, opened
from tests.support.graph_pages import graph_page


def test_opening_a_row_selects_it_and_asks_for_its_files_as_the_commits_own() -> None:
    model, request = opened()
    assert model.graph.selected == f"{1:040x}"
    assert (
        request.slot == "files"
        and request.commit is not None
        and request.commit.key == CommitKey("all", REVISION, f"{1:040x}")
    )
    reading = model.commit
    assert reading is not None and reading.commit.commit_id == "id-1"
    assert reading.model.scope == "commit" and reading.model.head == f"{1:040x}"
    assert model.files == loaded("a", scope="branch").files  # the list is not touched
    assert model.files_load == loaded("a", scope="branch").files_load


def test_a_row_without_an_id_is_only_selected_and_an_unknown_row_changes_nothing() -> None:
    plain = graph_model(ids=False)
    selected, request = open_commit(plain, "all", REVISION, f"{1:040x}", fresh())
    assert request is None and selected.commit is None and selected.graph.selected == f"{1:040x}"
    model = graph_model()
    assert open_commit(model, "all", REVISION, "f" * 40, fresh()) == (model, None)
    assert open_commit(model, "all", "older", f"{1:040x}", fresh()) == (model, None)
    assert open_commit(model, "branch", REVISION, f"{1:040x}", fresh()) == (model, None)


def test_the_same_commit_again_asks_for_nothing_and_another_replaces_the_pane() -> None:
    model, first = opened()
    same, request = open_commit(model, "all", REVISION, f"{1:040x}", fresh())
    assert request is None and same.commit is model.commit
    other, second = open_commit(model, "all", REVISION, f"{2:040x}", fresh())
    assert second is not None and second.commit != first.commit
    assert other.commit is not None and other.commit.key.oid == f"{2:040x}"
    assert arrive(other, first) is other  # the first commit's reply finds another commit open


def test_replies_fill_the_commits_files_and_never_the_lists() -> None:
    model, request = opened()
    model = arrive(model, request)
    reading = model.commit
    assert reading is not None
    assert [f.file_id for f in reading.model.files] == ["f1", "f2"]
    assert reading.model.base_revision == "p" * 40 and reading.model.snapshot == "commit-1"
    assert model.snapshot == loaded("a", scope="branch").snapshot


def test_file_steps_are_the_commits_and_their_requests_carry_its_key() -> None:
    model, request = opened()
    model = arrive(model, request)
    picked, fetch = commit_step(model, lambda inner: m.request_diff(m.select_file(inner, "f2")))
    assert fetch is not None and fetch.slot == "diff" and fetch.commit == request.commit
    assert picked.commit is not None and picked.commit.model.selected_file == "f2"
    assert picked.selected_file is None


@pytest.mark.parametrize("change", ["mode", "refresh", "files", "scope"])
def test_a_commit_closes_when_its_graph_context_changes(change: str) -> None:
    model, _ = opened()
    if change == "mode":
        model = m.set_graph_mode(model, "branch")[0]
    elif change == "refresh":
        model = m.begin_refresh(model)
    elif change == "files":
        model = m.set_view(model, "files")[0]
    else:
        model = m.set_scope(model, "working")
    assert model.commit is None
    assert close_commit(opened()[0]).commit is None


def test_a_graph_check_or_older_page_keeps_the_open_commit() -> None:
    model, _ = opened()
    model, request = m.request_graph(model, check=True)
    assert request is not None
    after = m.receive_graph(model, request.token, graph_page(snapshot="moved"), check=True)
    assert after.commit is not None and after.updated_available


def test_resume_asks_again_for_what_was_in_flight() -> None:
    model, _ = opened()
    revived, request = resume(model)
    assert request is not None and request.slot == "files" and request.commit is not None
    assert revived.commit is not None and model.commit is not None
    assert revived.commit.model.files_load.token > model.commit.model.files_load.token
    ready, first = opened()
    ready = arrive(ready, first)
    assert resume(ready)[1] is None
    chosen, diff = commit_step(ready, lambda i: m.request_diff(m.select_file(i, "f1")))
    assert diff is not None
    again = resume(chosen)
    assert again[1] is not None and again[1].slot == "diff"
    assert resume(graph_model())[1] is None


def test_a_commit_is_asked_about_as_a_whole_and_never_by_file_or_line() -> None:
    model, request = opened()
    model = arrive(model, request)
    assert model.commit is not None
    inner = model.commit.model
    scope = target(inner, "commit")
    assert scope.kind == "commit" and scope.commit == f"{1:040x}" and scope.base == "p" * 40
    assert scope.snapshot == "commit-1" and scope.label.startswith("This commit")
    assert target(inner, "branch") == scope
    assert validate(inner, scope) == ""
    for kind in ("file", "hunk", "line"):
        with pytest.raises(ValueError):
            target(inner, kind)
    moved = replace(inner, snapshot="commit-2")
    assert validate(moved, scope) != ""
    with pytest.raises(ValueError):
        target(replace(inner, head=None), "commit")


def test_a_bookmark_carries_the_open_commit_back() -> None:
    model, request = opened()
    model = arrive(model, request)
    bookmark = ReadingBookmark.capture(model)
    assert bookmark.commit is model.commit
    fresh = m.select(m.ChangesModel(), "a", model.worktree_id)
    seeded = bookmark.seed(fresh)
    assert seeded.commit is model.commit and seeded.view == "graph"
    assert ReadingBookmark.capture(replace(model, view="files")).commit is None


def test_a_reply_to_an_earlier_opening_of_the_same_commit_is_not_this_openings() -> None:
    first, old = opened()
    reopened, current = open_commit(close_commit(first), "all", REVISION, f"{1:040x}", fresh())
    assert current is not None and old.commit is not None and current.commit is not None
    assert current.commit.key == old.commit.key and current.commit != old.commit
    # The old opening's failure arrives first, then the current opening's success.
    after_failure = commit_receive(
        reopened, old.commit, lambda inner: m.fail(inner, old.slot, old.token, "busy")
    )
    assert after_failure is reopened
    completed = arrive(after_failure, current)
    assert completed.commit is not None
    assert completed.commit.model.files_load.status == "ready"
    assert completed.commit.model.snapshot == "commit-1"
    # And the old opening's success cannot fill the new one either.
    assert arrive(reopened, old) is reopened


def test_going_to_another_commit_and_back_is_a_new_opening_too() -> None:
    first, old = opened(1)
    other, _ = open_commit(first, "all", REVISION, f"{2:040x}", fresh())
    again, current = open_commit(other, "all", REVISION, f"{1:040x}", fresh())
    assert current is not None and old.commit is not None and current.commit is not None
    assert current.commit.generation != old.commit.generation
    stale = commit_receive(
        again, old.commit, lambda inner: m.fail(inner, old.slot, old.token, "unavailable")
    )
    assert stale is again
    done = arrive(stale, current).commit
    assert done is not None and done.model.snapshot == "commit-1"


def test_the_loader_never_hands_out_an_opening_number_twice() -> None:
    from relaydeck.client.screens.changes.parts.commit_loader import CommitLoader

    class Host:
        pass

    loader = CommitLoader(Host())  # type: ignore[arg-type]
    numbers = [loader.next_generation() for _ in range(5)]
    assert len(set(numbers)) == 5 and numbers == sorted(numbers)
    loader.resume_after(40)  # a commit restored from a bookmark keeps its own number
    assert loader.next_generation() == 41
    loader.resume_after(3)
    assert loader.next_generation() == 42


def test_a_bookmark_names_the_screen_level_it_was_left_at() -> None:
    model, request = opened()
    assert ReadingBookmark.capture(graph_model()).section() == "changes"
    assert ReadingBookmark.capture(model).section() == "commit"
    model = arrive(model, request)
    picked, _ = commit_step(model, lambda i: m.request_diff(m.select_file(i, "f1")))
    assert ReadingBookmark.capture(picked).section() == "commit-file"
