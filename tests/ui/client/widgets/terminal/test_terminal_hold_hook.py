"""The phone terminal keeps its keyboard-down height while the keyboard is up."""

from __future__ import annotations

from typing import Any

import flet as ft

from relaydeck.client.widgets.terminal.keyboard_overlap import use_terminal_hold
from tests.support.host_harness import render_component, run

STEPS: dict[str, tuple[float, float, float, bool, bool]] = {
    # key: (host_height, detail_width, keyboard_inset, keyboard_up, phone)
    "down": (700.0, 390.0, 0.0, False, True),
    "up": (380.0, 390.0, 320.0, True, True),
    "up-no-inset": (380.0, 390.0, 0.0, True, True),
    "rotated": (200.0, 844.0, 300.0, True, True),
    "not-phone": (380.0, 390.0, 320.0, True, False),
}


@ft.component
def Probe() -> Any:
    step, set_step = ft.use_state("down")
    height, width, inset, keyboard_up, phone = STEPS[step]
    held = use_terminal_hold(height, width, inset, keyboard_up, phone)
    return ft.Column(
        controls=[
            ft.Text(key="held", value=str(held)),
            *(
                ft.Button(name, key=f"step-{name}", on_click=lambda _e, n=name: set_step(n))
                for name in STEPS
            ),
        ]
    )


def test_the_keyboard_down_height_is_held_only_on_a_phone_with_the_keyboard_up() -> None:
    async def scenario() -> None:
        app = await render_component(Probe)

        async def at(step: str) -> float:
            await app.set_props_and_fire(f"step-{step}", {}, "click")
            return float(app.find("held").value)

        assert float(app.find("held").value) == 0.0
        # The page shrank for the keyboard: the terminal keeps its 700 px, anchored at the bottom.
        assert await at("up") == 700.0
        # Focus without a measured keyboard: nothing to hold.
        assert await at("up-no-inset") == 0.0
        # A rotation with the keyboard up discards the baseline.
        assert await at("rotated") == 0.0
        assert await at("down") == 0.0
        assert await at("not-phone") == 0.0

    run(scenario())
