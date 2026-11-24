"""Native text editors and search fields."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypedDict, Unpack, cast

import flet as ft
from flet.components.hooks.use_ref import MutableRef

from relaydeck.client.design_system import tokens as T


class TextInputProps(TypedDict, total=False):
    """Consumed native field properties; omitted values retain SDK defaults."""

    key: ft.KeyValue | None
    value: str
    label: ft.StrOrControl | None
    label_style: ft.TextStyle | None
    hint_text: str | None
    hint_style: ft.TextStyle | None
    text_size: float | None
    text_style: ft.TextStyle | None
    color: ft.ColorValue | None
    cursor_color: ft.ColorValue | None
    fill_color: ft.ColorValue | None
    filled: bool | None
    border: ft.ControlStateValue[ft.InputBorder] | None
    border_color: ft.ColorValue | None
    border_radius: ft.BorderRadiusValue | None
    focused_border_color: ft.ColorValue | None
    content_padding: ft.PaddingValue | None
    height: float | None
    size_constraints: ft.BoxConstraints | None
    expand: bool | int | None
    disabled: bool
    visible: bool
    dense: bool | None
    autofocus: bool
    can_request_focus: bool
    fit_parent_size: bool
    enable_interactive_selection: bool
    enable_suggestions: bool
    autocorrect: bool
    capitalization: ft.TextCapitalization | None
    smart_dashes_type: bool
    smart_quotes_type: bool
    multiline: bool
    min_lines: int | None
    max_lines: int | None
    max_length: int | None
    shift_enter: bool
    selection: ft.TextSelection | None
    prefix: ft.StrOrControl | None
    prefix_icon: ft.IconData | ft.Control | None
    suffix: ft.StrOrControl | None
    suffix_icon: ft.IconData | ft.Control | None
    suffix_icon_size_constraints: ft.BoxConstraints | None
    helper: ft.StrOrControl | None
    helper_max_lines: int | None
    error: ft.StrOrControl | None
    error_style: ft.TextStyle | None
    error_max_lines: int | None
    tooltip: str | ft.Tooltip | None
    data: object
    on_change: ft.ControlEventHandler[ft.TextField] | None
    on_focus: ft.ControlEventHandler[ft.TextField] | None
    on_blur: ft.ControlEventHandler[ft.TextField] | None
    on_submit: ft.ControlEventHandler[ft.TextField] | None
    on_tap_outside: ft.ControlEventHandler[ft.TextField] | None
    on_selection_change: ft.EventHandler[ft.TextSelectionChangeEvent[ft.TextField]] | None


def TextInput(
    *,
    ref: ft.Ref[ft.TextField] | MutableRef[ft.TextField] | None = None,
    **props: Unpack[TextInputProps],
) -> ft.TextField:
    """A native editor retaining caller keys, handlers, refs and selection."""
    # Native initialization only assigns ref.current, which both carriers support.
    return ft.TextField(ref=cast(ft.Ref[ft.TextField] | None, ref), **props)


def SearchField(
    *,
    ref: ft.Ref[ft.TextField] | MutableRef[ft.TextField] | None = None,
    **props: Unpack[TextInputProps],
) -> ft.TextField:
    """A native search editor with caller-owned labels and affordances."""
    return TextInput(ref=ref, **props)


def FilterField(
    value: str,
    on_change: Callable[[str], Any],
    *,
    hint: str,
    key: str,
) -> Any:
    """48px rounded filter field with a leading search glyph."""
    return SearchField(
        key=key,
        value=value,
        hint_text=hint,
        height=T.MIN_TOUCH,
        text_size=15,
        color=T.TEXT,
        cursor_color=T.PRIMARY_TEXT_LIGHT,
        hint_style=ft.TextStyle(color=T.TEXT_MUTED),
        prefix_icon=ft.Icons.SEARCH,
        filled=True,
        fill_color=T.CARD,
        border_radius=24,
        border_color=T.BORDER,
        focused_border_color=T.PRIMARY_TEXT,
        content_padding=ft.Padding.symmetric(horizontal=16, vertical=12),
        on_change=lambda e: on_change(str(e.control.value or "")),
    )


__all__ = ["FilterField", "SearchField", "TextInput", "TextInputProps"]
