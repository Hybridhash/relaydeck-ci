"""Native buttons with caller-owned content, styling and actions."""

from typing import Any, Literal, TypedDict, Unpack, cast, overload

import flet as ft

type ButtonVariant = Literal["standard", "text", "filled"]


class _CommonProps[ControlT: ft.Control](TypedDict, total=False):
    key: ft.KeyValue | None
    tooltip: str | ft.Tooltip | None
    width: float | None
    height: float | None
    style: ft.ButtonStyle | None
    expand: bool | int | None
    disabled: bool
    visible: bool
    icon: ft.IconData | ft.Control | None
    action: ft.ClientAction | list[ft.ClientAction] | None
    data: object
    on_click: ft.ControlEventHandler[ControlT] | None
    on_long_press: ft.ControlEventHandler[ControlT] | None


class TextButtonProps(_CommonProps[ft.TextButton]):
    """Properties consumed by native text-button callers."""


class ButtonProps(_CommonProps[ft.Button], total=False):
    bgcolor: ft.ColorValue | None
    color: ft.ColorValue | None


class FilledButtonProps(_CommonProps[ft.Button], total=False):
    bgcolor: ft.ColorValue | None
    color: ft.ColorValue | None


class _DispatchProps(_CommonProps[Any], total=False):
    bgcolor: ft.ColorValue | None
    color: ft.ColorValue | None


@overload
def Button(
    content: ft.StrOrControl | None = None,
    *,
    variant: Literal["text"],
    **props: Unpack[TextButtonProps],
) -> ft.TextButton: ...


@overload
def Button(
    content: ft.StrOrControl | None = None,
    *,
    variant: Literal["filled"],
    **props: Unpack[FilledButtonProps],
) -> ft.FilledButton: ...


@overload
def Button(
    content: ft.StrOrControl | None = None,
    *,
    variant: Literal["standard"] = "standard",
    **props: Unpack[ButtonProps],
) -> ft.Button: ...


def Button(
    content: ft.StrOrControl | None = None,
    *,
    variant: ButtonVariant = "standard",
    **props: Unpack[_DispatchProps],
) -> ft.Button | ft.TextButton:
    """Retain the requested native class and its SDK defaults without wrapping handlers."""
    if variant == "text":
        return ft.TextButton(content, **cast(TextButtonProps, props))
    if variant == "filled":
        return ft.FilledButton(content, **cast(FilledButtonProps, props))
    if variant == "standard":
        return ft.Button(content, **cast(ButtonProps, props))
    raise ValueError("Unknown button variant")
