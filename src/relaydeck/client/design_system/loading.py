"""Loading bars with an explicit motion preference supplied by the page owner."""

from __future__ import annotations

import asyncio
from typing import Any

import flet as ft

STEP_SECONDS = 0.45
PHASE_SECONDS = STEP_SECONDS  # how long each phase is held
_PATTERN = (0.4, 0.8, 0.55, 1.0)
_PHASES = len(_PATTERN)


def bar_fractions(phase: int, *, animated: bool) -> tuple[float, ...]:
    """The bars' heights as fractions of the icon's height. Static: the resting pattern."""
    if not animated:
        return _PATTERN
    return tuple(_PATTERN[(index + phase) % _PHASES] for index in range(_PHASES))


@ft.component
def LoadingBars(
    *, color: str, size: float = 28, reduce_motion: bool = False, key: str | None = None
) -> Any:
    """Four bars on one baseline. The animated one advances a phase every `STEP_SECONDS`."""
    animated = not reduce_motion
    phase, set_phase = ft.use_state(0)

    def run() -> None:
        if not animated:
            return

        async def tick() -> None:
            while True:
                await asyncio.sleep(PHASE_SECONDS)
                set_phase(lambda value: (value + 1) % _PHASES)

        task_ref.append(asyncio.create_task(tick()))

    task_ref: list[asyncio.Task[None]] = []

    def stop() -> None:
        for task in task_ref:
            task.cancel()
        task_ref.clear()

    ft.use_effect(run, dependencies=[animated], cleanup=stop)
    bar_w = max(3.0, size / 6)
    bars: list[ft.Control] = [
        ft.Container(
            width=bar_w,
            height=max(2.0, size * fraction),
            bgcolor=color,
            border_radius=2,
            animate=ft.Animation(int(STEP_SECONDS * 1000), ft.AnimationCurve.EASE_IN_OUT)
            if animated
            else None,
        )
        for fraction in bar_fractions(phase, animated=animated)
    ]
    return ft.Container(
        key=key or "usage-loading-icon",
        width=size,
        height=size,
        data={"animated": animated},
        alignment=ft.Alignment.BOTTOM_CENTER,
        content=ft.Row(
            controls=bars,
            spacing=max(1.0, bar_w / 2),
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.END,
        ),
    )
