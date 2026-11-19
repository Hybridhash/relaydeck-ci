"""Which files the reader has marked Viewed. Memory only, for the app session."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from typing import NamedTuple

MAX_MARKS = 10_000


class ViewKey(NamedTuple):
    server: str
    account: str
    host: str
    worktree_id: str
    scope: str
    base_revision: str | None
    file_id: str
    content_revision: str


class ViewContext(NamedTuple):
    """Everything about a view except the file: one list on screen."""

    server: str
    account: str
    host: str
    worktree_id: str
    scope: str
    base_revision: str | None

    def key(self, file_id: str, content_revision: str) -> ViewKey:
        return ViewKey(*self, file_id, content_revision)


class ReviewStateStore:
    def __init__(self, max_marks: int = MAX_MARKS) -> None:
        self._max = max_marks
        self._marks: OrderedDict[ViewKey, None] = OrderedDict()

    def __len__(self) -> int:
        return len(self._marks)

    def __repr__(self) -> str:
        return f"ReviewStateStore({len(self)} marks)"

    def is_viewed(self, key: ViewKey) -> bool:
        return key in self._marks

    def set_viewed(self, key: ViewKey, viewed: bool) -> None:
        if not viewed:
            self._marks.pop(key, None)
            return
        self._marks[key] = None
        self._marks.move_to_end(key)
        while len(self._marks) > self._max:
            self._marks.popitem(last=False)

    def toggle(self, key: ViewKey) -> bool:
        """Flip the mark and return the new value."""
        viewed = not self.is_viewed(key)
        self.set_viewed(key, viewed)
        return viewed

    def retain(self, context: ViewContext, current: Mapping[str, str]) -> None:
        """Drop this view's marks that no longer hold. `current` maps each file id now in the
        list to its content revision.
        """
        stale = [
            key
            for key in self._marks
            if key[:6] == context and current.get(key.file_id) != key.content_revision
        ]
        for key in stale:
            del self._marks[key]

    def forget_worktree(self, server: str, account: str, host: str, worktree_id: str) -> None:
        for key in [k for k in self._marks if k[:4] == (server, account, host, worktree_id)]:
            del self._marks[key]

    def clear_all(self) -> None:
        self._marks.clear()
