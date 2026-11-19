"""Heading text and the shared eyebrow/title hierarchy."""

import json
import math
from collections.abc import Sequence
from typing import overload

import flet as ft
from flet.components.component import Component

from relaydeck.client.design_system import tokens as T
from relaydeck.client.design_system.fonts import FAMILY_INTERFACE, FAMILY_MONO
from relaydeck.client.design_system.primitives import text_style

type HeadingControl = ft.Control | Component


def _heading_key(control: ft.Control) -> str | None:
    key = control.key
    if isinstance(key, ft.ValueKey | ft.ScrollKey):
        key = key.value
    if key is None:
        return None
    identity: list[str | int | bool]
    if isinstance(key, str):
        identity = ["heading", "str", key]
    elif isinstance(key, bool | int):
        identity = ["heading", "number", int(key), 1]
    elif math.isfinite(key):
        numerator, denominator = key.as_integer_ratio()
        identity = ["heading", "number", numerator, denominator]
    elif math.isnan(key):
        # A NaN dictionary key retains identity only when its object is reused.
        identity = ["heading", "nan", id(key)]
    else:
        identity = ["heading", "infinity", key > 0]
    return json.dumps(identity)


def _heading(control: ft.Control) -> ft.Semantics:
    return ft.Semantics(
        key=_heading_key(control),
        header=True,
        content=control,
        expand=control.expand,
        expand_loose=control.expand_loose,
        col=control.col,
        visible=control.visible,
        disabled=control.disabled,
    )


@overload
def ScreenHeader(title: Component) -> Component: ...


@overload
def ScreenHeader(
    title: str | ft.Control,
    *,
    key: str | None = None,
    style: ft.TextStyle | None = None,
    color: str | None = None,
    size: float | None = None,
    weight: ft.FontWeight | None = None,
    max_lines: int | None = None,
    no_wrap: bool = False,
    overflow: ft.TextOverflow = ft.TextOverflow.CLIP,
    expand: bool | int | None = None,
    tooltip: str | None = None,
) -> ft.Control: ...


def ScreenHeader(
    title: str | HeadingControl,
    *,
    key: str | None = None,
    style: ft.TextStyle | None = None,
    color: str | None = None,
    size: float | None = None,
    weight: ft.FontWeight | None = None,
    max_lines: int | None = None,
    no_wrap: bool = False,
    overflow: ft.TextOverflow = ft.TextOverflow.CLIP,
    expand: bool | int | None = None,
    tooltip: str | None = None,
) -> HeadingControl:
    """Annotate native titles; components annotate their native roots in their renderer."""
    if isinstance(title, Component):
        return title
    if not isinstance(title, str):
        return _heading(title)
    return _heading(
        ft.Text(
            title,
            key=key,
            style=style,
            color=color,
            size=size,
            weight=weight,
            max_lines=max_lines,
            no_wrap=no_wrap,
            overflow=overflow,
            expand=expand,
            tooltip=tooltip,
        )
    )


def SectionHeader(
    eyebrow: str,
    title: str,
    *,
    key: str | None = "settings-header",
    title_key: str | None = "destination-title",
    expand: bool | int | None = None,
    eyebrow_no_wrap: bool = False,
    title_no_wrap: bool = False,
    title_overflow: ft.TextOverflow = ft.TextOverflow.CLIP,
    after_title: Sequence[ft.Control] = (),
) -> ft.Column:
    """Mono eyebrow over an interface title, with optional supporting controls."""
    return ft.Column(
        key=key,
        spacing=0,
        expand=expand,
        controls=[
            ft.Text(
                eyebrow,
                style=text_style(FAMILY_MONO, T.TYPE_EYEBROW, T.TEXT_MUTED, 0.06),
                no_wrap=eyebrow_no_wrap,
            ),
            ScreenHeader(
                title,
                key=title_key,
                style=text_style(FAMILY_INTERFACE, T.TYPE_TITLE, T.TEXT, -0.02),
                no_wrap=title_no_wrap,
                overflow=title_overflow,
            ),
            *after_title,
        ],
    )
