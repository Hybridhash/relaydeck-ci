"""The overlap hook keeps its no-keyboard baseline only while the keyboard is down."""

from __future__ import annotations

from typing import Any

import flet as ft

from relaydeck.client.features.terminal.layout_budget import SAFARI_ACCESSORY_BAR
from relaydeck.client.widgets.terminal.keyboard_overlap import use_keyboard_overlap
from tests.support.host_harness import render_component, run

STEPS: dict[str, tuple[float, float, float, bool]] = {
    # key: (detail_height, detail_width, keyboard_inset, keyboard_up)
    "down": (480.0, 1000.0, 0.0, False),
    "up-no-sample": (318.0, 1000.0, 0.0, True),
    "up-sampled": (318.0, 1000.0, 346.0, True),
    "rotated": (318.0, 600.0, 346.0, True),
    "down-rotated": (300.0, 600.0, 0.0, False),
    "up-rotated-sampled": (138.0, 600.0, 346.0, True),
}


@ft.component
def Probe() -> Any:
    step, set_step = ft.use_state("down")
    height, width, inset, keyboard_up = STEPS[step]
    overlap = use_keyboard_overlap(height, width, inset, keyboard_up=keyboard_up, split=True)
    return ft.Column(
        controls=[
            ft.Text(key="overlap", value=str(overlap)),
            *(
                ft.Button(name, key=f"step-{name}", on_click=lambda _e, n=name: set_step(n))
                for name in STEPS
            ),
        ]
    )


def test_baseline_moves_only_while_the_keyboard_is_down() -> None:
    async def scenario() -> None:
        app = await render_component(Probe)

        async def at(step: str) -> float:
            await app.set_props_and_fire(f"step-{step}", {}, "click")
            return float(app.find("overlap").value)

        assert float(app.find("overlap").value) == 0.0
        # Keyboard up, page removed 162 px, no sample yet: the bar is assumed uncovered.
        assert await at("up-no-sample") == SAFARI_ACCESSORY_BAR
        # The sample arrives: 346 minus the 162 the page removed against the kept baseline.
        assert await at("up-sampled") == 346.0 - 162.0
        # Rotation with the keyboard up discards the baseline: the whole inset counts.
        assert await at("rotated") == 346.0
        # Keyboard down in the new orientation: a fresh baseline of 300.
        assert await at("down-rotated") == 0.0
        assert await at("up-rotated-sampled") == 346.0 - 162.0

    run(scenario())
