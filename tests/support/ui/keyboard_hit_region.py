"""Exact expected-tree delta for the keyboard button's larger padded hit region."""

from copy import deepcopy
from typing import Any


def keyboard_hit_region_expected(original: dict[str, Any]) -> dict[str, Any]:
    expected = deepcopy(original)

    def walk(nodes: list[dict[str, Any]]):
        for node in nodes:
            yield node
            yield from walk(node.get("children", []))

    nodes = list(walk(expected["roots"]))
    buttons = [node for node in nodes if node.get("key") == "agent-workflow-toggle"]
    rows = [node for node in nodes if node.get("key") == "agent-tab-row"]
    assert len(buttons) == len(rows) == 1
    button, row = buttons[0], rows[0]
    assert button["type"] == "IconButton" and row["type"] == "Row"
    assert {key: button["props"][key] for key in ("width", "height", "padding", "icon_size")} == {
        "width": 32,
        "height": 32,
        "padding": 0,
        "icon_size": 16,
    }
    assert row["props"]["height"] == 40
    semantics = [node for node in row["children"] if node.get("key") == "agent-keyboard-semantics"]
    assert len(semantics) == 1 and semantics[0]["props"]["label"] == "Terminal keyboard"
    assert semantics[0]["children"] == [button]
    button["props"].update(width=48, height=48, padding=16)
    row["props"]["height"] = 48
    return expected
