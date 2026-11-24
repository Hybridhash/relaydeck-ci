"""The snapshot serialiser itself: stable, default-free, volatile-free."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import flet as ft
import pytest

from tests.support.host_harness import render_component, run
from tests.support.ui.snapshot import (
    UPDATE_ENV,
    assert_matches_golden,
    scrub,
    snapshot_json,
    snapshot_tree,
)


@ft.component
def _Inner(label: str) -> Any:
    return ft.Text(label, key="inner-text", size=13)


@ft.component
def _Demo() -> Any:
    return ft.Column(
        controls=[
            ft.Text("Hello", key="greeting", size=14, color="#112233"),
            ft.Container(
                key="box",
                padding=8,
                bgcolor="#445566",
                border_radius=6,
                data={"scratch": 1},
                on_click=lambda _e: None,
                content=_Inner("Inside"),
            ),
            ft.Text("generated", key="row-123e4567-e89b-12d3-a456-426614174000"),
        ],
        spacing=4,
    )


def _find(node: dict[str, Any], key: str) -> dict[str, Any]:
    if node.get("key") == key:
        return node
    for child in node.get("children", []):
        try:
            return _find(child, key)
        except KeyError:
            continue
    raise KeyError(key)


def test_snapshot_records_type_key_and_non_default_props() -> None:
    async def scenario() -> None:
        app = await render_component(_Demo)
        tree = snapshot_tree(app)
        greeting = _find(tree["roots"][0], "greeting")
        assert greeting["type"] == "Text"
        assert greeting["props"]["value"] == "Hello"
        assert greeting["props"]["size"] == 14
        assert greeting["props"]["color"] == "#112233"
        # Defaults are not recorded.
        assert "visible" not in greeting["props"]
        assert "italic" not in greeting["props"]

    run(scenario())


def test_snapshot_excludes_data_handlers_and_ids_but_keeps_slots() -> None:
    async def scenario() -> None:
        app = await render_component(_Demo)
        text = snapshot_json(app)
        assert "scratch" not in text
        assert "on_click" not in text
        assert '"_i"' not in text
        box = _find(snapshot_tree(app)["roots"][0], "box")
        assert box["props"]["padding"] == 8
        (child,) = box["children"]
        assert child["slot"] == "content"
        assert child["type"] == "Component:_Inner"
        inner = _find(child, "inner-text")
        assert inner["props"]["value"] == "Inside"

    run(scenario())


def test_snapshot_is_identical_across_renders() -> None:
    async def scenario() -> None:
        first = snapshot_json(await render_component(_Demo))
        second = snapshot_json(await render_component(_Demo))
        assert first == second

    run(scenario())


def test_golden_compare_passes_fails_and_regenerates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        app = await render_component(_Demo)
        with pytest.raises(AssertionError, match="missing golden"):
            assert_matches_golden(app, tmp_path, "demo")
        monkeypatch.setenv(UPDATE_ENV, "1")
        assert_matches_golden(app, tmp_path, "demo")  # writes the golden
        monkeypatch.delenv(UPDATE_ENV)
        assert_matches_golden(app, tmp_path, "demo")  # now it compares equal
        golden = tmp_path / "demo.json"
        golden.write_text(golden.read_text().replace("Hello", "Changed"))
        with pytest.raises(AssertionError, match="differs at line"):
            assert_matches_golden(app, tmp_path, "demo")

    run(scenario())


def test_scrub_neutralises_generated_material_only() -> None:
    assert scrub("row-123e4567-e89b-12d3-a456-426614174000") == "row-<uuid>"
    assert scrub("cursor-" + "ab12" * 9) == "cursor-<hex>"
    assert scrub("at 1762000000000 ms") == "at <epoch-ms> ms"
    assert scrub("as of 12 s ago · 3 min ago") == "as of <age> ago · <age> ago"
    assert scrub("draft-editor-17") == "draft-editor-<serial>"
    assert scrub("agent-workflow-direct-typing:reset:4") == (
        "agent-workflow-direct-typing:reset:<epoch>"
    )
    assert scrub("agent-home-mac:term-1") == "agent-home-mac:term-1"
    assert scrub("Version 0.9.1") == "Version 0.9.1"
