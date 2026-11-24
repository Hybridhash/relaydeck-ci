"""Find controls that need a bounded height inside a vertically scrolling page.

A scrolling column gives its children unbounded height. A vertical list view, a scrolling
column, or a child that expands along that axis then has no height to fill, and Flutter rejects
the layout. The walk follows the mounted tree the way the snapshot serialiser does and stops at
any control with a fixed height, whose subtree is bounded again.
"""

from __future__ import annotations

from typing import Any

import flet as ft

from tests.support.ui.snapshot import _control_slots, _type_name
from tests.support.ui.tree_path import _path_to

LIST_COMPONENTS = frozenset({"Component:VirtualList"})


def _fixed_height(control: Any) -> bool:
    height = getattr(control, "height", None)
    return isinstance(height, int | float) and not isinstance(height, bool) and height > 0


def _label(control: Any) -> str:
    key = getattr(control, "key", None)
    return f"{_type_name(control)}[{key}]" if key is not None else _type_name(control)


def _is_vertical_list(control: Any) -> bool:
    if isinstance(control, ft.ListView):
        return not getattr(control, "horizontal", False)
    return _type_name(control) in LIST_COMPONENTS


def _expands_vertically(control: Any, parent: Any) -> bool:
    """Whether `expand` takes the space left on a vertical axis. Under a Row it is horizontal."""
    if not getattr(control, "expand", None):
        return False
    if isinstance(parent, ft.Row) and not isinstance(parent, ft.Column):
        return False
    if isinstance(parent, ft.ListView) and getattr(parent, "horizontal", False):
        return False
    return True


def _violations(control: Any, parent: Any, path: str, found: list[str]) -> None:
    here = f"{path} > {_label(control)}"
    if _is_vertical_list(control):
        found.append(f"{here}: vertical list under a scrolling column")
    if isinstance(control, ft.Column) and getattr(control, "scroll", None) is not None:
        found.append(f"{here}: scrolling column under a scrolling column")
    if _expands_vertically(control, parent):
        found.append(f"{here}: expand={control.expand!r} under an unbounded height")
    if _fixed_height(control):
        return
    for _slot, child in _control_slots(control):
        _violations(child, control, here, found)


def scroll_column_above(app: Any, key: str) -> Any:
    """The nearest vertically scrolling column that contains the control with `key`."""
    for ancestor in reversed(_path_to(app, key)[:-1]):
        if isinstance(ancestor, ft.Column) and ancestor.scroll is not None:
            return ancestor
    raise AssertionError(f"{key!r} is not inside a scrolling column")


def unbounded_height_violations(root: Any) -> list[str]:
    """Paths of controls under `root` (a vertically scrolling control) that need a bounded
    height, with no fixed-height control between them and `root`. Empty when the page is safe."""
    found: list[str] = []
    for _slot, child in _control_slots(root):
        _violations(child, root, _label(root), found)
    return found
