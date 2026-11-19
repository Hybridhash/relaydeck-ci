"""Local presentation catalog; no product store or transport is constructed."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import flet as ft
from flet.components.hooks.use_ref import MutableRef

from relaydeck.client.design_system.appearance import build_theme
from relaydeck.client.design_system.fonts import page_fonts
from relaydeck.client.design_system.loading import LoadingBars
from relaydeck.client.design_system.theme import palette
from relaydeck.devtools.catalog_controls import Sample, build
from relaydeck.devtools.catalog_model import (
    ENTRIES,
    STATES,
    THEMES,
    WIDTHS,
    Case,
    State,
    Theme,
    cases,
)
from relaydeck.devtools.control_tree import iter_controls


@ft.component
def Specimen(case: Case) -> Any:
    value, set_value = ft.use_state(case.label)
    checked, set_checked = ft.use_state(case.state == "normal")
    selected, set_selected = ft.use_state(
        "overview" if case.entry in ("bottom-bar", "console-rail") else "first"
    )
    count, set_count = ft.use_state(123456789012345 if case.state == "long-text" else 16)
    actions, set_actions = ft.use_state(0)
    subject_ref: MutableRef[ft.Container] = ft.use_ref()
    focus_task: MutableRef[asyncio.Task[None]] = ft.use_ref()
    page = ft.context.page
    sample = Sample(
        case,
        value,
        checked,
        selected,
        count,
        set_value,
        set_checked,
        set_selected,
        lambda delta: set_count(lambda old: old + delta),
        lambda: set_actions(lambda old: old + 1),
    )
    control = build(sample)
    t = palette(case.dark)

    def overlay() -> None:
        if isinstance(control, ft.SnackBar):
            page.show_dialog(control)

    def close_overlay() -> None:
        if isinstance(control, ft.SnackBar):
            page.pop_dialog()

    ft.use_effect(overlay, dependencies=[case.key], cleanup=close_overlay)

    def focus() -> None:
        if case.state != "focus" or not case.definition.focusable:
            return

        async def request() -> None:
            await asyncio.sleep(0)
            for node in iter_controls([subject_ref.current], active_only=True):
                if isinstance(node, ft.TextButton | ft.Button | ft.TextField):
                    request_focus = cast(Callable[[], Awaitable[None]], node.focus)
                    await request_focus()
                    return

        focus_task.current = asyncio.create_task(request())

    def stop_focus() -> None:
        if focus_task.current is not None:
            focus_task.current.cancel()
            focus_task.current = None

    ft.use_effect(focus, dependencies=[case.key], cleanup=stop_focus)
    subject = (
        ft.Text("Floating toast preview", key="sample-overlay-note")
        if isinstance(control, ft.SnackBar)
        else control
    )
    root = ft.Container(
        key="catalog-subject",
        disabled=case.state == "disabled",
        content=subject,
    )
    subject_ref.current = root
    messages: list[Any] = []
    if case.state == "error" and not case.definition.native_error:
        messages.append(
            ft.Semantics(
                label="Example validation error",
                live_region=True,
                content=ft.Text("Example validation error", color=t.danger),
                key="catalog-error-context",
            )
        )
    if case.state == "loading" and case.entry != "loading-bars":
        messages.append(
            ft.Semantics(
                label="Loading example",
                live_region=True,
                content=LoadingBars(color=t.accent_text, reduce_motion=True),
                key="catalog-loading-context",
            )
        )
    return ft.Column(
        controls=[
            root,
            *messages,
            ft.Text(f"Example actions: {actions}", key="catalog-actions", color=t.muted),
        ],
        spacing=16,
    )


@ft.component
def Preview(case: Case) -> Any:
    t = palette(case.dark)
    return ft.Container(
        key=f"catalog-preview-{case.key}",
        width=case.width,
        padding=16,
        bgcolor=t.bg,
        theme=build_theme(case.dark),
        theme_mode=ft.ThemeMode.DARK if case.dark else ft.ThemeMode.LIGHT,
        content=ft.Column(
            spacing=16,
            controls=[
                ft.Text(case.definition.symbol, size=22, color=t.text, key="catalog-title"),
                ft.Text(
                    f"{case.width} px · {case.theme} · {case.state}",
                    color=t.muted,
                    key="catalog-case",
                ),
                ft.Text(case.note, color=t.muted, key="catalog-state-note"),
                Specimen(case),
            ],
        ),
    )


@ft.component
def Catalog(initial: Case | None = None) -> Any:
    case, set_case = ft.use_state(initial or Case(ENTRIES[0].id))

    def entry(event: Any) -> None:
        set_case(replace(case, entry=str(event.control.value)))

    def width(event: Any) -> None:
        set_case(replace(case, width=int(event.control.value)))

    def theme(event: Any) -> None:
        value: Theme = "dark" if event.control.value == "dark" else "light"
        set_case(replace(case, theme=value))

    def state(event: Any) -> None:
        value = next(item for item in STATES if item == event.control.value)
        set_case(replace(case, state=value))

    return ft.Column(
        expand=True,
        scroll=ft.ScrollMode.AUTO,
        controls=[
            ft.Text("Design system catalog", size=24),
            ft.Row(
                wrap=True,
                controls=[
                    ft.Dropdown(
                        label="Control",
                        key="catalog-entry",
                        value=case.entry,
                        width=240,
                        options=[ft.DropdownOption(e.id, e.symbol) for e in ENTRIES],
                        on_select=entry,
                    ),
                    ft.Dropdown(
                        label="Width",
                        key="catalog-width",
                        value=str(case.width),
                        width=120,
                        options=[ft.DropdownOption(str(w)) for w in WIDTHS],
                        on_select=width,
                    ),
                    ft.Dropdown(
                        label="Theme",
                        key="catalog-theme",
                        value=case.theme,
                        width=120,
                        options=[ft.DropdownOption(t) for t in THEMES],
                        on_select=theme,
                    ),
                    ft.Dropdown(
                        label="State",
                        key="catalog-state",
                        value=case.state,
                        width=160,
                        options=[ft.DropdownOption(s) for s in STATES],
                        on_select=state,
                    ),
                ],
            ),
            ft.Row(scroll=ft.ScrollMode.AUTO, controls=[Preview(case)]),
        ],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list", action="store_true", help="List every catalog case without starting a page"
    )
    parser.add_argument("--control", choices=[e.id for e in ENTRIES], default=ENTRIES[0].id)
    parser.add_argument("--width", type=int, choices=WIDTHS, default=320)
    parser.add_argument("--theme", choices=THEMES, default="light")
    parser.add_argument("--state", choices=STATES, default="normal")
    parser.add_argument("--port", type=int, default=8555)
    args = parser.parse_args(argv)
    if args.list:
        for case in cases():
            print(case.key)
        return 0
    theme: Theme = "dark" if args.theme == "dark" else "light"
    state: State = next(s for s in STATES if s == args.state)
    initial = Case(args.control, args.width, theme, state)

    def page_main(page: ft.Page) -> None:
        page.title = "Design system catalog"
        page.fonts = page_fonts()
        page.theme = build_theme(False)
        page.dark_theme = build_theme(True)
        page.padding = 16
        page.render(Catalog, initial)

    ft.run(
        page_main,
        host="127.0.0.1",
        port=args.port,
        view=ft.AppView.WEB_BROWSER,
        assets_dir=str(Path(__file__).parents[1] / "client/assets"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
