"""ReviewStateStore: marks belong to one exact view and live in memory only."""

from __future__ import annotations

import pytest

from relaydeck.client.features.changes.review_state import ReviewStateStore, ViewContext, ViewKey

CONTEXT = ViewContext("srv", "acct", "host", "wt-1", "working", "base-1")
KEY = CONTEXT.key("file-1", "rev-1")


def test_a_mark_is_found_until_it_is_removed() -> None:
    store = ReviewStateStore()
    assert not store.is_viewed(KEY)
    store.set_viewed(KEY, True)
    assert store.is_viewed(KEY) and len(store) == 1
    store.set_viewed(KEY, False)
    assert not store.is_viewed(KEY) and len(store) == 0
    store.set_viewed(KEY, False)  # removing an absent mark is harmless


def test_toggle_flips_and_reports_the_new_value() -> None:
    store = ReviewStateStore()
    assert store.toggle(KEY) is True and store.is_viewed(KEY)
    assert store.toggle(KEY) is False and not store.is_viewed(KEY)


@pytest.mark.parametrize(
    "change",
    [
        {"server": "other"},
        {"account": "other"},
        {"host": "other"},
        {"worktree_id": "wt-2"},
        {"scope": "branch"},
        {"base_revision": "base-2"},
        {"base_revision": None},
        {"file_id": "file-2"},
        {"content_revision": "rev-2"},
    ],
    ids=["server", "account", "host", "worktree", "scope", "base", "no_base", "file", "content"],
)
def test_a_mark_does_not_apply_when_any_part_of_the_key_differs(
    change: dict[str, str | None],
) -> None:
    store = ReviewStateStore()
    store.set_viewed(KEY, True)
    assert not store.is_viewed(KEY._replace(**change))


def test_the_key_has_exactly_the_documented_parts_in_order() -> None:
    assert ViewKey._fields == (
        "server",
        "account",
        "host",
        "worktree_id",
        "scope",
        "base_revision",
        "file_id",
        "content_revision",
    )
    assert tuple(KEY) == ("srv", "acct", "host", "wt-1", "working", "base-1", "file-1", "rev-1")


def test_retain_drops_marks_for_changed_or_removed_files_only() -> None:
    store = ReviewStateStore()
    other_branch = ViewContext("srv", "acct", "host", "wt-1", "branch", "base-1")
    for file_id in ("a", "b", "c"):
        store.set_viewed(CONTEXT.key(file_id, "r1"), True)
    store.set_viewed(other_branch.key("a", "r1"), True)
    store.retain(CONTEXT, {"a": "r1", "b": "r2"})  # b changed, c left the list
    assert store.is_viewed(CONTEXT.key("a", "r1"))
    assert not store.is_viewed(CONTEXT.key("b", "r1"))
    assert not store.is_viewed(CONTEXT.key("c", "r1"))
    assert store.is_viewed(other_branch.key("a", "r1"))  # another view is not touched


def test_a_changed_file_can_be_marked_again_under_its_new_revision() -> None:
    store = ReviewStateStore()
    store.set_viewed(CONTEXT.key("a", "r1"), True)
    store.retain(CONTEXT, {"a": "r2"})
    assert not store.is_viewed(CONTEXT.key("a", "r2"))
    store.set_viewed(CONTEXT.key("a", "r2"), True)
    assert store.is_viewed(CONTEXT.key("a", "r2")) and len(store) == 1


def test_switching_scope_or_base_hides_marks_and_switching_back_shows_them_again() -> None:
    store = ReviewStateStore()
    store.set_viewed(KEY, True)
    assert not store.is_viewed(CONTEXT._replace(scope="branch").key("file-1", "rev-1"))
    assert not store.is_viewed(CONTEXT._replace(base_revision="base-2").key("file-1", "rev-1"))
    assert store.is_viewed(CONTEXT.key("file-1", "rev-1"))


def test_forget_worktree_drops_only_that_worktree() -> None:
    store = ReviewStateStore()
    keep = ViewContext("srv", "acct", "host", "wt-2", "working", None).key("a", "r")
    other_account = ViewContext("srv", "someone", "host", "wt-1", "working", None).key("a", "r")
    for key in (KEY, CONTEXT._replace(scope="branch").key("f", "r"), keep, other_account):
        store.set_viewed(key, True)
    store.forget_worktree("srv", "acct", "host", "wt-1")
    assert len(store) == 2 and store.is_viewed(keep) and store.is_viewed(other_account)


def test_clear_all_forgets_everything_for_logout_and_revocation() -> None:
    store = ReviewStateStore()
    for index in range(5):
        store.set_viewed(CONTEXT.key(f"f{index}", "r"), True)
    store.clear_all()
    assert len(store) == 0 and not store.is_viewed(CONTEXT.key("f0", "r"))


def test_the_store_is_bounded_and_forgets_the_oldest_mark_first() -> None:
    store = ReviewStateStore(max_marks=3)
    keys = [CONTEXT.key(f"f{i}", "r") for i in range(4)]
    for key in keys[:3]:
        store.set_viewed(key, True)
    store.set_viewed(keys[0], True)  # marking again makes it the newest
    store.set_viewed(keys[3], True)
    assert not store.is_viewed(keys[1])
    assert all(store.is_viewed(keys[i]) for i in (0, 2, 3))


def test_repr_shows_no_identifiers() -> None:
    store = ReviewStateStore()
    store.set_viewed(CONTEXT.key("secret-file-id", "r"), True)
    text = repr(store)
    assert "secret-file-id" not in text and "wt-1" not in text and "1" in text
