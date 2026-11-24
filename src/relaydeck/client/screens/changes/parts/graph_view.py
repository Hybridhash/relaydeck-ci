"""Graph chips, notices and the virtualised, append-only list of rows."""

from __future__ import annotations

import flet as ft

import relaydeck.client.screens.changes.parts.graph_rows as graph_rows
from relaydeck.client.design_system import tokens as t
from relaydeck.client.design_system.buttons import Button
from relaydeck.client.design_system.lists.virtual_list import Place, Row, VirtualList
from relaydeck.client.features.changes.api import ChangesActions, ChangesModel, GraphAnchor
from relaydeck.client.screens.changes.parts.graph_rows import AGE_BUCKET_MS
from relaydeck.client.screens.changes.parts.graph_style import STYLES, layout_of
from relaydeck.client.screens.changes.parts.header import graph_summary
from relaydeck.contracts.git import GraphMode


@ft.component
def GraphView(
    model: ChangesModel,
    actions: ChangesActions,
    *,
    phone: bool,
    wide: bool,
    now_ms: int,
    compact: bool = False,
) -> ft.Control:
    graph, load = model.graph, model.graph_load
    style = STYLES[layout_of(phone=phone, wide=wide, compact=compact)]
    stacked, height = style.stacked, style.row
    # The gutter fits the lanes the history uses (two at least), within the layout's cap.
    lanes = max(2, min(style.lane_cap, graph.lanes))
    # Badge room per layout: what is left beside the oid and the age (stacked), or the share of a
    # one-line row that leaves the subject the larger part.
    budget = (150.0 if phone else 90.0) if stacked else 260.0
    total = len(graph.commits)
    revision = graph.page.snapshot if graph.page else ""
    index = next(
        (i for i, c in enumerate(graph.commits) if graph.anchor and c.oid == graph.anchor.oid), None
    )
    place = Place(index, graph.anchor.offset) if index is not None and graph.anchor else None

    def moved(at: Place) -> None:
        actions.graph_anchor(
            graph.mode,
            revision,
            GraphAnchor(graph.commits[at.index].oid, at.offset),
        )

    def chip(label: str, mode: GraphMode) -> ft.Control:
        return Button(
            label,
            variant="text",
            key=f"graph-mode-{mode}",
            data={"selected": graph.mode == mode},
            height=t.MIN_TOUCH,
            # A 48 dp touch target drawn with the boards' pill shape and type.
            style=ft.ButtonStyle(
                color=t.ON_PRIMARY if graph.mode == mode else t.TEXT,
                bgcolor=t.PRIMARY_FILL if graph.mode == mode else t.RAISED,
                shape=ft.StadiumBorder(),
                text_style=ft.TextStyle(size=12.5, weight=ft.FontWeight.W_600),
            ),
            on_click=lambda _: actions.graph_mode(mode),
        )

    band: list[ft.Control] = [chip("All branches", "all"), chip("This branch", "branch")]
    if compact:  # a short screen has one band for the chips and the summary
        band.append(graph_summary(model, now_ms, newest_first=True, inline=True))
    controls: list[ft.Control] = [ft.Row(controls=band)]  # inset below
    if graph.page is not None and graph.page.limit_reason == "ref_limit":
        controls.append(
            ft.Text("Some branches are not shown", key="graph-ref-limit", color=t.TEXT_MUTED)
        )
    if graph.history_limited:
        controls.append(
            ft.Text("History limit reached", key="graph-history-limit", color=t.TEXT_MUTED)
        )
    if load.status == "error":
        controls.append(
            ft.Text("Could not load the graph.", key="graph-error", color=t.TEXT_SECONDARY)
        )
        controls.append(
            Button(
                "Retry",
                variant="text",
                key="graph-retry",
                height=t.MIN_TOUCH,
                on_click=lambda _: actions.more_graph() if graph.next else actions.refresh(),
            )
        )
    elif not graph.commits:
        controls.append(ft.Text("Loading graph…", key="graph-loading", color=t.TEXT_MUTED))
    mode, bucket = graph.mode, now_ms // AGE_BUCKET_MS

    def described(i: int) -> Row:
        commit, layout = graph.commits[i], graph.rows[i]
        selected = graph.selected == commit.oid
        return Row(
            commit.oid,
            (mode, revision, selected, style, lanes, budget, bucket),
            lambda: graph_rows.build_row(
                model,
                actions,
                commit,
                layout,
                style=style,
                lanes=lanes,
                now_ms=now_ms,
                budget=budget,
                selected=selected,
            ),
        )

    footer: list[ft.Control] = []
    if graph.next is not None:
        footer.append(
            Button(
                "Show older commits",
                variant="text",
                key="changes-more-graph",
                height=t.MIN_TOUCH,
                disabled=load.status == "loading",
                on_click=lambda _: actions.more_graph(),
            )
        )
    # The chips and notices sit on the header's left edge; the rows run to their own gutter.
    controls = [
        ft.Container(
            key="graph-chips" if index == 0 else None,
            padding=ft.Padding.symmetric(horizontal=t.PAD_PHONE),
            content=c,
        )
        for index, c in enumerate(controls)
    ]
    controls.append(
        VirtualList(
            name="changes-graph",
            count=total,
            height=height,
            row=described,
            context=(graph.mode, revision, height),
            place=place,
            on_scroll=moved,
            footer=footer,
        )
    )
    return ft.Column(expand=True, spacing=0, controls=controls)
