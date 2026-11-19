"""What a windowed list sends the client: the space above its first built row, and the height of
every child, which the client places at the sum of the heights before it."""

from __future__ import annotations

from typing import Any


def leading_space(app: Any, name: str) -> float:
    """The list's top padding, the height of the rows that are not built above the window."""
    return float(app.find(name).padding.top)


def extents(app: Any, name: str) -> list[float]:
    """The height sent for each child of the list, in the children's order."""
    listing = app.find(name)
    sent = list(listing.item_extents or [])
    assert len(sent) == len(listing.controls), "one extent per child"
    return [float(e) for e in sent]


def placed(app: Any, name: str) -> list[tuple[str, float]]:
    """Each child's key and where the client draws its top: the list's leading space plus the
    extents of the children before it."""
    at, found = leading_space(app, name), []
    for child, extent in zip(app.find(name).controls, extents(app, name), strict=True):
        found.append((str(child.key), at))
        at += extent
    return found
