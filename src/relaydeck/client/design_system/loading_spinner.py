"""Native progress indicators with caller-owned progress and visibility."""

from typing import TypedDict, Unpack

import flet as ft


class SpinnerProps(TypedDict, total=False):
    key: ft.KeyValue | None
    visible: bool
    value: float | None
    width: float | None
    height: float | None
    stroke_width: float | None
    color: ft.ColorValue | None
    semantics_label: str | None
    semantics_value: float | None


def Spinner(**props: Unpack[SpinnerProps]) -> ft.ProgressRing:
    """Retain native defaults; an explicit progress value renders a static indicator."""
    return ft.ProgressRing(**props)
