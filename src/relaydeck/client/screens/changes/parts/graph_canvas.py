"""Native Canvas rendering for a bounded projection of logical graph lanes."""

from __future__ import annotations

import flet as ft
import flet.canvas as cv

from relaydeck.client.design_system import tokens as t
from relaydeck.client.design_system.theme import graph_palette
from relaydeck.client.features.changes.api import GraphRow, project
from relaydeck.client.screens.changes.parts.graph_style import GraphStyle

HEAD_RADIUS = 6.0
MERGE_RADIUS = 7.0
HEAD_RING = 10.5


def gutter_width(style: GraphStyle, lanes: int) -> int:
    return style.lane_pad * 2 + (lanes - 1) * style.lane_gap


def graph_canvas(row: GraphRow, *, style: GraphStyle, lanes: int) -> ft.Container:
    height = style.row
    projection = project(row, lanes)
    palette = graph_palette()

    def color(index: int) -> str:
        return palette[index] if index < 2 else palette[2 + (index - 2) % (len(palette) - 2)]

    def x(lane: int) -> float:
        return float(style.lane_pad + projection.slot(lane) * style.lane_gap)

    mid = height / 2
    shapes: list[cv.Shape] = []
    bundle: dict[str, tuple[float, str]] = {}  # the one stub each way for all hidden lanes
    for segment in row.segments:
        a, b = x(segment.start), x(segment.end)
        ink = color(segment.color)
        if projection.hidden and segment.start >= lanes - 1 and segment.end >= lanes - 1:
            # Hidden lanes share one stub above and one below the marker, however many there are;
            # it stops short of the marker, and the count does not imply shared ancestry.
            if segment.kind in ("pass", "up", "in"):
                bundle.setdefault("up", (a, ink))
            if segment.kind in ("pass", "down", "out"):
                bundle.setdefault("down", (b, ink))
            continue
        if segment.kind == "pass":
            elements: list[cv.Path.PathElement] = [cv.Path.MoveTo(a, 0), cv.Path.LineTo(a, height)]
        elif segment.kind == "up":
            elements = [cv.Path.MoveTo(a, 0), cv.Path.LineTo(b, mid)]
        elif segment.kind == "down":
            elements = [cv.Path.MoveTo(a, mid), cv.Path.LineTo(b, height)]
        elif segment.kind == "in":
            elements = [cv.Path.MoveTo(a, 0), cv.Path.CubicTo(a, mid * 0.9, b, mid * 0.25, b, mid)]
        else:
            elements = [
                cv.Path.MoveTo(a, mid),
                cv.Path.CubicTo(a, mid * 1.75, b, mid * 1.1, b, height),
            ]
        shapes.append(
            cv.Path(
                elements,
                paint=ft.Paint(
                    color=ink,
                    stroke_width=2.4,
                    style=ft.PaintingStyle.STROKE,
                    stroke_cap=ft.StrokeCap.ROUND,
                ),
            )
        )
    for direction, (at, ink) in bundle.items():
        top, bottom = (0, mid - 11) if direction == "up" else (mid + 11, height)
        shapes.append(cv.Line(at, top, at, bottom, paint=ft.Paint(color=ink, stroke_width=2)))
    ink = t.TEXT_MUTED if row.dim else color(row.color)
    radius = MERGE_RADIUS if row.merge else HEAD_RADIUS
    shapes.append(
        cv.Circle(
            x(row.lane),
            mid,
            radius,
            paint=ft.Paint(
                color=ink if row.ahead else t.GROUND,
                style=ft.PaintingStyle.FILL,
            ),
        )
    )
    shapes.append(
        cv.Circle(
            x(row.lane),
            mid,
            radius,
            paint=ft.Paint(
                color=ink,
                stroke_width=2.4,
                style=ft.PaintingStyle.STROKE,
            ),
        )
    )
    if row.head:
        shapes.append(
            cv.Circle(
                x(row.lane),
                mid,
                HEAD_RING,
                paint=ft.Paint(
                    color=ft.Colors.with_opacity(0.35, ink),
                    stroke_width=2,
                    style=ft.PaintingStyle.STROKE,
                ),
            )
        )
    width = gutter_width(style, lanes)
    controls: list[ft.Control] = [cv.Canvas(shapes=shapes, width=width, height=height)]
    if projection.hidden:
        controls.append(
            ft.Container(
                alignment=ft.Alignment.BOTTOM_RIGHT,
                content=ft.Text(f"+{projection.hidden}", size=9, color=t.TEXT_SECONDARY),
            )
        )
    return ft.Container(width=width, height=height, content=ft.Stack(controls=controls))
