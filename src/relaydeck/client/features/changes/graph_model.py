"""Pure graph page, mode and freshness transitions."""

from __future__ import annotations

from dataclasses import replace

from relaydeck.client.features.changes.graph_layout import LaneState, append, seed
from relaydeck.client.features.changes.graph_state import GraphAnchor, GraphState
from relaydeck.client.features.changes.loads import _current, _ready, _start, _with_load
from relaydeck.client.features.changes.state import ChangesModel, Load, Request, Slot
from relaydeck.contracts.git import MAX_HISTORY, GraphMode, GraphPage


def request_graph(
    model: ChangesModel,
    *,
    more: bool = False,
    check: bool = False,
) -> tuple[ChangesModel, Request | None]:
    if model.agent_id is None or model.scope != "branch":
        return model, None
    if check:
        if model.graph.page is None or model.graph_check.status == "loading":
            return model, None
        started, token = _start(model, "graph_check")
        return started, Request("graph_check", token, mode=model.graph.mode, check=True)
    if more and model.graph.next is None or model.graph_load.status == "loading":
        return model, None
    started, token = _start(model, "graph")
    return started, Request(
        "graph",
        token,
        cursor=model.graph.next if more else None,
        mode=model.graph.mode,
        check=check,
    )


def set_graph_mode(model: ChangesModel, mode: GraphMode) -> tuple[ChangesModel, Request | None]:
    if mode == model.graph.mode:
        return model, None
    previous = replace(model.graph, other=None)
    target = model.graph.other
    if target is None or target.mode != mode:
        target = GraphState(mode=mode)
    changed = replace(
        model,
        graph=replace(target, other=previous),
        graph_load=Load(model.graph_load.token + 1),
        graph_check=Load(model.graph_check.token + 1),
        commit=None,  # a commit belongs to one mode of one graph
        commit_lost=False,
    )
    return request_graph(changed, check=target.page is not None)


def receive_graph(
    model: ChangesModel,
    token: int,
    page: GraphPage,
    *,
    more: bool = False,
    check: bool = False,
) -> ChangesModel:
    slot: Slot = "graph_check" if check else "graph"
    if not _current(model, slot, token):
        return model
    ready, graph = _ready(model, slot), model.graph
    if page.mode != graph.mode:
        return _with_load(model, slot, Load(token, "error", "upstream_incompatible"))
    if check:
        changed = graph.page is not None and page.snapshot != graph.page.snapshot
        return replace(ready, updated_available=model.updated_available or changed)
    if more and (graph.page is None or graph.page.snapshot != page.snapshot):
        return replace(ready, updated_available=True)
    existing = graph.commits if more else ()
    if len(existing) + len(page.commits) > MAX_HISTORY:
        return _with_load(model, slot, Load(token, "error", "upstream_incompatible"))
    checkpoint = (
        graph.checkpoints[-1] if more else seed(page.mode, page.head, page.base_oid, page.split_oid)
    )
    try:
        rows, after = append(checkpoint, page.commits)
    except ValueError:
        return _with_load(model, slot, Load(token, "error", "upstream_incompatible"))
    kept = GraphState(
        mode=graph.mode,
        page=graph.page if more else page,
        commits=existing + page.commits,
        rows=(graph.rows if more else ()) + rows,
        checkpoints=_settled(graph.checkpoints if more else ()) + (after,),
        next=page.next,
        selected=graph.selected if more else None,
        anchor=graph.anchor if more else None,
        history_limited=graph.history_limited or page.limit_reason == "history_limit",
        other=graph.other,
    )
    return replace(ready, graph=kept)


def _settled(checkpoints: tuple[LaneState, ...]) -> tuple[LaneState, ...]:
    """Only the newest checkpoint is ever resumed from; older ones keep their lanes but not the
    set of commits seen, which would otherwise be held once per page."""
    return tuple(replace(c, seen=frozenset()) if c.seen else c for c in checkpoints)


def select_graph(model: ChangesModel, mode: GraphMode, revision: str, oid: str) -> ChangesModel:
    if (
        model.graph.mode != mode
        or model.graph.page is None
        or model.graph.page.snapshot != revision
    ):
        return model
    if not any(c.oid == oid for c in model.graph.commits):
        return model
    return replace(model, graph=replace(model.graph, selected=oid))


def graph_anchor(
    model: ChangesModel, mode: GraphMode, revision: str, anchor: GraphAnchor
) -> ChangesModel:
    if (
        model.graph.mode != mode
        or model.graph.page is None
        or model.graph.page.snapshot != revision
    ):
        return model
    return replace(model, graph=replace(model.graph, anchor=anchor))
