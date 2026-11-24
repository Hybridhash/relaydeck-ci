"""Runs the Changes model's requests through a source and re-renders on every reply."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import flet as ft

import relaydeck.client.features.changes.api as m
from relaydeck.client.features.changes.api import (
    DEFAULT_RETRY_AFTER,
    MAX_RETRY_AFTER,
    ChangesFailure,
    ChangesSource,
    ReadingBookmark,
    close_commit,
    commit_actions,
    graph_anchor,
    open_commit,
    resume,
)
from relaydeck.client.features.changes.api import ChangesActions as ChangesActions
from relaydeck.client.screens.changes.parts.commit_loader import CommitLoader
from relaydeck.contracts.git import GraphMode, WorktreeSummary

GRAPH_CHECK_INTERVAL_S = 10.0


class _Runner:
    """Holds the model and the tasks in flight for one mounted screen."""

    def __init__(self, source: ChangesSource, host_id: str, rerender: Callable[[], None]) -> None:
        self.bookmark: ReadingBookmark | None = None
        self.source = source
        self.host_id = host_id
        self.model = m.ChangesModel()
        self.tasks: set[asyncio.Task[None]] = set()
        self._rerender = rerender
        self.commits = CommitLoader(self)

    def apply(self, step: tuple[m.ChangesModel, m.Request | None] | m.ChangesModel) -> None:
        model, request = step if isinstance(step, tuple) else (step, None)
        previous = self.model
        if model is previous and request is None:
            return
        self.model = model
        self.commits.observe()
        if request is not None:
            self._spawn(self._fetch(request))
        if (
            model.graph_check != previous.graph_check
            and replace(model, graph_check=previous.graph_check) == previous
        ):
            return
        self._rerender()

    def reload(self) -> None:
        """Apply the newer revision and load again whatever the reader is looking at."""
        self.apply(m.request_files(m.begin_refresh(self.model)))
        if self.model.view == "graph":
            self.apply(m.request_graph(self.model))

    def remember(self, anchor: m.Anchor) -> None:
        """Note the reader's place without redrawing: scrolling must not rebuild the screen."""
        self.model = m.set_anchor(self.model, anchor)

    def _spawn(self, work: Any) -> None:
        task = asyncio.create_task(work)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _fetch(self, request: m.Request) -> None:
        model = self.model
        worktree = model.worktree_id
        if worktree is None:
            return
        if request.commit is not None:
            await self.commits.fetch(request, worktree)
            return
        try:
            if request.slot == "files":
                page = await self.source.changes(
                    self.host_id, worktree, model.scope, request.cursor
                )
                more = request.cursor is not None
                self.apply(m.receive_files(self.model, request.token, page, more=more))
                if self.bookmark is not None:
                    self.model = self.bookmark.restore(self.model)
                    if self.model.selected_file is not None or not m.reselecting(self.model):
                        self.bookmark = None
                if self.model.selected_file is not None and (
                    self.model.diff is None or not self.model.diff.pages
                ):
                    self.apply(m.request_diff(self.model))  # the reader's file, in the new list
                elif m.reselecting(self.model):
                    self.apply(m.request_files(self.model, more=True))  # look on, page by page
            elif request.slot == "diff" and model.selected_file is not None:
                diff = await self.source.file_diff(
                    self.host_id, worktree, model.selected_file, request.cursor, request.load
                )
                self.apply(m.receive_diff(self.model, request.token, diff, request.page or 0))
            elif request.slot in ("graph", "graph_check"):
                await self._fetch_graph(worktree, request)
            elif request.slot == "bases":
                bases, after = await self.source.bases(self.host_id, worktree, request.cursor)
                more = request.cursor is not None
                self.apply(m.receive_bases(self.model, request.token, bases, after, more=more))
            elif request.slot == "save" and request.base_id is not None:
                name = await self.source.set_base(self.host_id, worktree, request.base_id)
                saved = m.receive_base_saved(self.model, request.token, name, None)
                self.apply(m.request_files(saved) if saved.scope == "branch" else saved)
                if saved.scope == "branch" and self.model.view == "graph":
                    self.apply(m.request_graph(self.model))
        except asyncio.CancelledError:
            raise
        except ChangesFailure as failure:
            self.apply(m.fail(self.model, request.slot, request.token, failure.reason))
            self._retry_later(request, failure)
        except Exception:  # noqa: BLE001 - shown as a fixed state; nothing from the error is kept
            self.apply(m.fail(self.model, request.slot, request.token, "unavailable"))

    async def _fetch_graph(self, worktree: str, request: m.Request) -> None:
        page = await self.source.graph(
            self.host_id, worktree, request.mode or self.model.graph.mode, request.cursor
        )
        self.apply(
            m.receive_graph(
                self.model,
                request.token,
                page,
                more=request.cursor is not None,
                check=request.check,
            )
        )
        if request.cursor is None and not request.check:
            self.commits.graph_arrived(request.token)

    def _retry_later(self, request: m.Request, failure: ChangesFailure) -> None:
        """A first list the Mac was too busy to give is asked for again when its window ends."""
        wait = DEFAULT_RETRY_AFTER.get(failure.reason)
        if request.slot != "files" or request.cursor is not None or wait is None:
            return
        if failure.retry_after_s is not None:
            wait = failure.retry_after_s
        self._spawn(self._ask_again(request.token, min(max(wait, 0.0), MAX_RETRY_AFTER)))

    async def _ask_again(self, token: int, delay: float) -> None:
        await asyncio.sleep(delay)
        load = self.model.files_load
        if load.token == token and load.status == "error" and self.model.snapshot is None:
            self.apply(m.request_files(self.model))

    def stop(self) -> None:
        for task in tuple(self.tasks):
            task.cancel()
        self.tasks.clear()


def use_changes(
    source: ChangesSource,
    host_id: str,
    agent_id: str,
    worktree_id: str | None,
    summary: WorktreeSummary | None = None,
    bookmark: ReadingBookmark | None = None,
    foreground: bool = True,
    graph_first: bool = False,
) -> tuple[m.ChangesModel, ChangesActions]:
    """The model for one agent's worktree and the actions that change it. The model starts over
    only when the worktree changes.
    """
    _tick, set_tick = ft.use_state(0)
    runner_ref: Any = ft.use_ref(None)

    def rerender() -> None:
        set_tick(lambda value: value + 1)

    if runner_ref.current is None:
        runner_ref.current = _Runner(source, host_id, rerender)
    runner: _Runner = runner_ref.current
    runner.source, runner.host_id = source, host_id

    opens_graph: Any = ft.use_ref(graph_first)

    def start() -> None:
        selected = m.select(runner.model, agent_id, worktree_id or None)
        if opens_graph.current and bookmark is None:  # a reload on a commit route: the graph
            selected = replace(selected, scope="branch", view="graph")
            opens_graph.current = not worktree_id  # kept until the worktree is known
        if bookmark is not None:
            selected = bookmark.seed(selected)
            runner.bookmark = bookmark
        runner.apply(m.request_files(selected) if worktree_id else selected)
        if selected.view == "graph" and selected.graph.page is None:
            runner.apply(m.request_graph(runner.model))
        elif selected.view == "graph" and selected.commit is not None:
            runner.commits.resume_after(selected.commit.generation)
            runner.apply(resume(runner.model))

    ft.use_effect(start, dependencies=[host_id, agent_id, worktree_id or None], cleanup=runner.stop)

    def fold() -> None:
        if summary is not None:
            runner.apply(m.apply_summary(runner.model, summary))

    ft.use_effect(fold, dependencies=[summary])

    reload = runner.reload

    def set_scope(scope: m.Scope) -> None:
        runner.apply(m.request_files(m.set_scope(runner.model, scope)))

    def select_file(file_id: str | None) -> None:
        if file_id is None:
            runner.apply(
                replace(
                    runner.model,
                    selected_file=None,
                    selected_path=None,
                    selected_hunk=None,
                    diff=None,
                )
            )
            return
        runner.apply(m.request_diff(m.select_file(runner.model, file_id)))

    def earlier_diff() -> None:
        page = m.earlier_page(runner.model)
        if page is not None:
            runner.apply(m.request_diff(runner.model, page=page))

    async def check_graph() -> None:
        while True:
            await asyncio.sleep(GRAPH_CHECK_INTERVAL_S)
            if runner.model.graph.page is not None:
                runner.apply(m.request_graph(runner.model, check=True))

    checking: Any = ft.use_ref(None)

    def watch_graph() -> None:
        if foreground and runner.model.view == "graph":
            checking.current = asyncio.create_task(check_graph())

    def stop_check() -> None:
        if checking.current is not None:
            checking.current.cancel()
            checking.current = None

    ft.use_effect(
        watch_graph, dependencies=[foreground, runner.model.view, worktree_id], cleanup=stop_check
    )

    def reader_opens(mode: GraphMode, revision: str, oid: str) -> None:
        runner.commits.reader_opened()
        runner.apply(
            open_commit(runner.model, mode, revision, oid, runner.commits.next_generation())
        )

    own = ChangesActions(
        refresh=reload,
        open_commit=reader_opens,
        close_commit=lambda: runner.apply(close_commit(runner.model)),
        graph_mode=lambda mode: runner.apply(m.set_graph_mode(runner.model, mode)),
        graph_anchor=lambda mode, revision, anchor: setattr(
            runner, "model", graph_anchor(runner.model, mode, revision, anchor)
        ),
        set_scope=set_scope,
        set_view=lambda view: runner.apply(m.set_view(runner.model, view)),
        select_file=select_file,
        step_file=lambda delta: runner.apply(m.request_diff(m.step_file(runner.model, delta))),
        more_files=lambda: runner.apply(m.request_files(runner.model, more=True)),
        more_diff=lambda: runner.apply(m.request_diff(runner.model)),
        earlier_diff=earlier_diff,
        more_graph=lambda: runner.apply(m.request_graph(runner.model, more=True)),
        load_diff=lambda: runner.apply(m.load_diff(runner.model)),
        open_bases=lambda: runner.apply(m.open_base_picker(runner.model)),
        close_bases=lambda: runner.apply(m.close_base_picker(runner.model)),
        more_bases=lambda: runner.apply(m.request_bases(runner.model, more=True)),
        save_base=lambda base_id: runner.apply(m.request_save_base(runner.model, base_id)),
        remember=runner.remember,
        current=lambda: runner.model,
        select_hunk=lambda place: runner.apply(replace(runner.model, selected_hunk=place)),
    )
    return runner.model, replace(own, commit=commit_actions(runner))
