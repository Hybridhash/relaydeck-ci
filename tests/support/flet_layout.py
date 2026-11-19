"""Layout checks the Python control tree can prove before Flutter renders it.

`expand` makes Flet wrap a control in Flutter's `Expanded`, which is valid only directly inside a
Row or Column. Anywhere else (a Container, a Stack, a ListView, a wrapping Row) a release build
throws a parent-data type error and draws a grey box instead of the subtree.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import flet as ft
from flet.components.component import Component
from flet.controls.base_control import BaseControl

_FLEX = (ft.Row, ft.Column, ft.View)  # a View lays its controls out in a column


def _children(control: Any) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    if not dataclasses.is_dataclass(control):
        return out
    for field in dataclasses.fields(control):
        if field.name.startswith("_") or field.name in ("parent", "page", "data"):
            continue
        value = getattr(control, field.name, None)
        if isinstance(value, BaseControl):
            out.append((field.name, value))
        elif isinstance(value, list | tuple):
            out.extend((field.name, item) for item in value if isinstance(item, BaseControl))
    return out


def _flex_parent(parent: Any, slot: str) -> bool:
    if not isinstance(parent, _FLEX) or slot != "controls":
        return False
    return not (isinstance(parent, ft.Row) and getattr(parent, "wrap", False))


def expand_violations(root: Any) -> list[str]:
    """Paths of controls that expand outside a non-wrapping Row or Column."""
    found: list[str] = []
    seen: set[int] = set()

    def check(child: Any, parent: Any, slot: str, path: str) -> None:
        here = f"{path}/{type(child).__name__}"
        key = getattr(child, "key", None)
        if key:
            here += f"[{key}]"
        body = getattr(child, "_b", None)
        if isinstance(child, Component):
            # A component is transparent: its body sits where the component sits.
            if isinstance(body, BaseControl):
                check(body, parent, slot, here)
            return
        if getattr(child, "expand", None) and not _flex_parent(parent, slot):
            found.append(f"{here} expands inside {type(parent).__name__}.{slot}")
        walk(child, here)

    def walk(control: Any, path: str) -> None:
        if id(control) in seen:
            return
        seen.add(id(control))
        for slot, child in _children(control):
            check(child, control, slot, path)

    walk(root, type(root).__name__)
    return found
