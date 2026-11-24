"""One graph row: lane gutter, ref badges, oid, age and subject; and a cache of built rows. A
row's controls depend only on its commit, its lane layout and a few view settings, so a built
row is reused as the same object until one of those changes.
"""

from __future__ import annotations

import flet as ft

from relaydeck.client.design_system import tokens as t
from relaydeck.client.design_system.badges import Badge
from relaydeck.client.design_system.fonts import FAMILY_MONO
from relaydeck.client.features.changes.api import ChangesActions, ChangesModel, GraphRow
from relaydeck.client.screens.changes.parts.graph_canvas import graph_canvas
from relaydeck.client.screens.changes.parts.graph_style import GraphStyle, badge_style
from relaydeck.client.screens.changes.parts.header import age_text
from relaydeck.contracts.git import GraphCommit, GraphRef

AGE_BUCKET_MS = 30_000  # a row's age text is rebuilt at most this often


def ref_text(ref: GraphRef, model: ChangesModel) -> str:
    if ref.kind == "head":
        return "HEAD" if ref.name == "HEAD" else f"HEAD · {ref.name}"
    if ref.kind == "base":
        return f"{ref.name} · base"
    if ref.kind == "split" and model.graph.mode == "branch":
        page = model.graph.page
        return f"split from {page.base_name}" if page else "split point"
    return ref.name


BADGE_CAP = 140.0  # dp; a longer name is cut with an ellipsis inside its own badge
BADGE_SHOWN = 4
_BADGE_CHAR = 6.2  # dp per character at the badge's size, an estimate used only to size badges


def _badge_width(text: str) -> float:
    return min(BADGE_CAP, len(text) * _BADGE_CHAR + 10)


def ref_badges(commit: GraphCommit, model: ChangesModel, *, budget: float | None = None) -> ft.Row:
    """The commit's refs, semantic ones first. Each badge is at most `BADGE_CAP` wide and cuts a
    longer name with an ellipsis; with a `budget` (dp) only as many as fit are shown, the first
    always, and the rest are counted."""
    badges: list[ft.Control] = []
    priority = {"head": 0, "base": 1, "split": 2}
    refs = sorted(commit.refs, key=lambda ref: priority.get(ref.kind, 3))
    used = 0.0
    for ref in refs[:BADGE_SHOWN]:
        text = ref_text(ref, model)
        width = _badge_width(text)
        if badges and budget is not None and used + width > budget:
            break
        used += width + 4
        look = badge_style(ref.kind)
        badges.append(
            Badge(
                width=width,
                padding=ft.Padding.symmetric(horizontal=5, vertical=2),
                border_radius=6,
                bgcolor=look.fill,
                border=ft.Border.all(1, look.edge),
                content=ft.Text(
                    text,
                    size=10,
                    color=look.ink,
                    max_lines=1,
                    no_wrap=True,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
            )
        )
    if len(refs) > len(badges):
        badges.append(ft.Text(f"+{len(refs) - len(badges)}", color=t.TEXT_MUTED, size=10))
    return ft.Row(spacing=4, controls=badges, tight=True, scroll=ft.ScrollMode.HIDDEN)


def build_row(
    model: ChangesModel,
    actions: ChangesActions,
    commit: GraphCommit,
    row: GraphRow,
    *,
    style: GraphStyle,
    lanes: int,
    now_ms: int,
    budget: float,
    selected: bool,
) -> ft.Control:
    page = model.graph.page
    revision = page.snapshot if page else ""
    stacked = style.stacked
    subject = ft.Text(
        commit.subject or "(no message)",
        size=style.subject,
        color=t.TEXT_MUTED if row.dim else t.TEXT,
        max_lines=1,
        no_wrap=True,
        overflow=ft.TextOverflow.ELLIPSIS,
    )
    badges = ref_badges(commit, model, budget=budget)
    oid = ft.Text(commit.oid[:7], font_family=FAMILY_MONO, size=style.oid, color=t.TEXT_MUTED)
    age = ft.Text(age_text(commit.time_ms, now_ms), size=style.age, color=t.TEXT_MUTED)
    # Never wider than the room it is given: whatever does not fit is clipped, not painted over.
    meta = ft.Row(spacing=7, tight=True, controls=[badges, oid, age])
    text: ft.Control = (
        ft.Column(
            spacing=3,
            alignment=ft.MainAxisAlignment.CENTER,
            controls=[ft.Container(clip_behavior=ft.ClipBehavior.HARD_EDGE, content=meta), subject],
        )
        if stacked
        else ft.Row(
            spacing=12,
            controls=[ft.Container(expand=True, content=subject), meta],
        )
    )
    labels = ", ".join(ref_text(r, model) for r in commit.refs)
    return ft.Semantics(
        selected=selected,
        button=True,
        label=f"{commit.subject}, {commit.oid[:7]}, {labels}",
        content=ft.Container(
            key=f"graph-row-{revision}-{commit.oid}",
            height=style.row,
            data={"selected": selected, "stacked": stacked},
            bgcolor=t.CARD_SELECTED if selected else t.GROUND,
            # A 3 dp violet bar marks the open row; the clear one keeps every row's text in place.
            border=ft.Border(
                left=ft.BorderSide(3, t.PRIMARY_TEXT if selected else ft.Colors.TRANSPARENT)
            ),
            padding=ft.Padding.only(right=t.PAD_PHONE),
            on_click=lambda _: actions.open_commit(model.graph.mode, revision, commit.oid),
            content=ft.Row(
                spacing=2,
                controls=[
                    graph_canvas(row, style=style, lanes=lanes),
                    ft.Container(expand=True, content=text),
                ],
            ),
        ),
    )
