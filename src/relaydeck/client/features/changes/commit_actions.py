"""The Changes actions bound to the open commit: its files, diff and copy, never the list's."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol

import relaydeck.client.features.changes.model as m
from relaydeck.client.features.changes.commit_model import Step, commit_step


class _Host(Protocol):
    model: m.ChangesModel

    def apply(self, step: tuple[m.ChangesModel, m.Request | None] | m.ChangesModel) -> None: ...


@dataclass(frozen=True, slots=True)
class CommitActions:
    """Reading actions for the open commit: its files, diff and copy, never the list's."""

    refresh: Callable[[], None]
    select_file: Callable[[str | None], None]
    step_file: Callable[[int], None]
    more_files: Callable[[], None]
    more_diff: Callable[[], None]
    earlier_diff: Callable[[], None]
    load_diff: Callable[[], None]
    remember: Callable[[m.Anchor], None]
    current: Callable[[], m.ChangesModel]
    select_hunk: Callable[[tuple[int, int]], None]


def commit_actions(host: _Host) -> CommitActions:
    """Actions for `host`'s open commit; refresh asks for the commit's first page again."""

    def run(step: Step) -> None:
        host.apply(commit_step(host.model, step))

    def current() -> m.ChangesModel:
        reading = host.model.commit
        return reading.model if reading is not None else m.ChangesModel(scope="commit")

    def select_file(file_id: str | None) -> None:
        if file_id is not None:
            run(lambda inner: m.request_diff(m.select_file(inner, file_id)))
            return
        run(
            lambda inner: replace(
                inner, selected_file=None, selected_path=None, selected_hunk=None, diff=None
            )
        )

    def remember(anchor: m.Anchor) -> None:
        host.model = commit_step(host.model, lambda inner: m.set_anchor(inner, anchor))[0]

    return CommitActions(
        refresh=lambda: run(m.request_files),
        select_file=select_file,
        step_file=lambda delta: run(lambda inner: m.request_diff(m.step_file(inner, delta))),
        more_files=lambda: run(lambda inner: m.request_files(inner, more=True)),
        more_diff=lambda: run(m.request_diff),
        earlier_diff=lambda: run(_earlier),
        load_diff=lambda: run(m.load_diff),
        remember=remember,
        current=current,
        select_hunk=lambda place: run(lambda inner: replace(inner, selected_hunk=place)),
    )


def _earlier(inner: m.ChangesModel) -> m.ChangesModel | tuple[m.ChangesModel, m.Request | None]:
    page = m.earlier_page(inner)
    return m.request_diff(inner, page=page) if page is not None else inner
