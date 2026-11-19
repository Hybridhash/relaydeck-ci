"""Usage screen and Console card."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Hashable
from dataclasses import replace
from typing import Any

import flet as ft

import relaydeck.client.features.usage.api as presentation_usage
import relaydeck.client.features.usage.api as s
import relaydeck.client.features.usage.ui as usage_hooks
from relaydeck.client.design_system.buttons import Button
from relaydeck.client.design_system.headers import ScreenHeader
from relaydeck.client.design_system.theme import UsageColors
from relaydeck.client.design_system.tokens import usage_palette
from relaydeck.client.design_system.typography import ui
from relaydeck.client.features.usage.api import STATUS_LOADING, UsageFetch, screen_state
from relaydeck.client.features.usage.ui import use_usage_feed
from relaydeck.client.screens.usage import model as sm
from relaydeck.client.screens.usage import pins as pin_store
from relaydeck.client.screens.usage.memo import PageMemo
from relaydeck.client.screens.usage.parts import cards, chart, dialogs, rows
from relaydeck.client.screens.usage.parts import primitives as p
from relaydeck.client.screens.usage.parts.providers import ProvidersDialog, ProvidersView
from relaydeck.client.screens.usage.parts.space_sheet import SpaceSheet

ALL_SPACES = ""  # the page's own state for "all spaces"; server filter is empty too
NOT_LINKED = "not_linked"  # a local selection: not-linked usage is its own scope
ALL_KINDS = ""
PERIOD_DAYS = {"today": 1, "7d": 7, "30d": 30, "90d": 90}
CHART_TITLES = {"today": "Hourly tokens", "90d": "Weekly tokens"}  # the chart's own grain


def _width_class(measured: float | None, wide: bool) -> str:
    if measured is None:
        return sm.WIDTH_WIDE if wide else sm.WIDTH_COMPACT
    return sm.width_class(measured)


_DEFAULT_WIDTH = {sm.WIDTH_COMPACT: 390.0, sm.WIDTH_MEDIUM: 768.0, sm.WIDTH_WIDE: 1180.0}


def _plot_width(measured: float | None, width_class: str) -> float:
    """The estimated pixel width of the chart's bars, from the measured page width: the gutters,
    the card padding and, on the wide grid, the chart card's share beside the plan card and the
    value axis. It sizes the value labels, so it errs small (a rotated label, never an overlap)."""
    page = measured if measured else _DEFAULT_WIDTH[width_class]
    if width_class == sm.WIDTH_WIDE:
        column = page - 2 * 36 - 16
        return max(200.0, column * 155 / 255 - 2 * 20 - 52)
    gutter = 2 * 20
    return max(200.0, page - gutter - 2 * 16)


def _filter(period: str, space: str, provider: str) -> s.Filter:
    space_value = s.ALL if space == ALL_SPACES else s.UNLINKED if space == NOT_LINKED else space
    return s.Filter(period=period, space=space_value, provider=provider or s.ALL)


def _server_space(space: str) -> str:
    return "" if space in (ALL_SPACES, NOT_LINKED) else space


def _section(
    title: str, *, tokens: UsageColors, key: str, link: str | None = None, on_link: Any = None
) -> ft.Row:
    controls: list[ft.Control] = [
        ScreenHeader(ui(title, color=tokens.text, size=18, weight=800, key=key))
    ]
    if link is not None:
        controls.append(ft.Container(expand=True))
        controls.append(
            ft.Container(
                key=f"{key}-link",
                height=44,
                alignment=ft.Alignment.CENTER_RIGHT,
                on_click=on_link,
                content=ui(link, color=tokens.link, size=14, weight=700, max_lines=1),
            )
        )
    return ft.Row(controls=controls, spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)


def _attention_block(
    screen: sm.ScreenModel, *, tokens: UsageColors, on_action: Any, on_more: Any
) -> list[ft.Control]:
    if not screen.attention.items:
        return []
    return [
        rows.attention_header(len(screen.attention.items), tokens=tokens),
        rows.attention_list(screen.attention, tokens=tokens, on_action=on_action, on_more=on_more),
    ]


def _body(
    screen: sm.ScreenModel,
    *,
    tokens: UsageColors,
    width: str,
    on_action: Any,
    on_see_spaces: Any,
    on_more: Any,
    on_manage: Any,
    plot_width: float | None = None,
    reduce_motion: bool = False,
    on_refresh_limits: Callable[[str], None] | None = None,
) -> list[ft.Control]:
    """The main page below the header. Compact and medium stack; wide lays out the spec's grid."""
    if screen.state == presentation_usage.STATE_LOADING:
        return [cards.skeleton(tokens=tokens, reduce_motion=reduce_motion)]
    if screen.state in (
        presentation_usage.STATE_CAPACITY,
        presentation_usage.STATE_PAGE_TOO_LARGE,
        presentation_usage.STATE_UNAVAILABLE,
        presentation_usage.STATE_DISABLED,
        presentation_usage.STATE_NO_PROVIDERS,
    ):
        tone = (
            "warn"
            if screen.state
            in (presentation_usage.STATE_CAPACITY, presentation_usage.STATE_PAGE_TOO_LARGE)
            else "normal"
        )
        return [
            cards.message_card(screen.message, tokens=tokens, key="usage-state-message", tone=tone)
        ]
    wide = width == sm.WIDTH_WIDE
    assert screen.summary is not None and screen.chart is not None  # noqa: S101
    empty_note = (
        [cards.empty_caption("No usage in this period", tokens=tokens, key="usage-empty")]
        if screen.empty
        else []
    )
    chart_card = p.card(
        [
            _section(
                screen.chart.title or CHART_TITLES.get(screen.period, "Daily tokens"),
                tokens=tokens,
                key="usage-chart-title",
            ),
            chart.UsageChart(chart=screen.chart, tokens=tokens, wide=wide, plot_width=plot_width),
        ],
        tokens=tokens,
        key="usage-chart-card",
        padding=20 if wide else 16,
    )
    plan_children: list[ft.Control] = []
    if wide:
        plan_children.extend(
            _attention_block(screen, tokens=tokens, on_action=on_action, on_more=on_more)
        )
        if screen.attention.items and screen.plan:
            plan_children.append(p.divider(tokens))
    plan_children.append(
        _section(
            "Plan limits", tokens=tokens, key="usage-plan-title", link="Manage", on_link=on_manage
        )
    )
    plan_children.extend(
        rows.limit_rows(screen.plan, tokens=tokens, on_refresh=on_refresh_limits)
        or [ui("No connected CLIs", color=tokens.text_muted, size=14, weight=600)]
    )
    plan_card = p.card(
        plan_children, tokens=tokens, key="usage-plan-card", padding=20 if wide else 16
    )

    spaces_card = None
    if screen.spaces is not None and not empty_note:
        spaces_card = p.card(
            [
                _section(
                    "Top spaces",
                    tokens=tokens,
                    key="usage-spaces-title",
                    # One link only: the header on a phone or tablet (the table has its own on
                    # wide).
                    link=f"See all {screen.space_total}"
                    if screen.spaces_more and not wide
                    else None,
                    on_link=on_see_spaces,
                ),
                (
                    rows.spaces_table(
                        screen.spaces,
                        tokens=tokens,
                        total=screen.space_total,
                        on_show_all=on_see_spaces,
                        show_all=screen.spaces_more,
                    )
                    if wide
                    else rows.ranked_list(
                        screen.spaces,
                        tokens=tokens,
                        provider=False,
                        key_prefix="usage-space",
                        on_rollup=on_see_spaces,
                    )
                ),
            ],
            tokens=tokens,
            key="usage-spaces-card",
            padding=20 if wide else 16,
        )
    providers_card = None
    if screen.providers is not None and not empty_note:
        provider_children: list[ft.Control] = [
            _section(
                "By provider",
                tokens=tokens,
                key="usage-providers-title",
                link=f"See all {len(screen.connected)}" if screen.connected else None,
                on_link=on_more,
            ),
            rows.ranked_list(
                screen.providers,
                tokens=tokens,
                provider=True,
                key_prefix="usage-provider",
            ),
        ]
        if screen.provider_zero:
            provider_children.append(
                ui(
                    screen.provider_zero,
                    color=tokens.text_muted,
                    size=12,
                    weight=600,
                    max_lines=2,
                )
            )
        providers_card = p.card(
            provider_children, tokens=tokens, key="usage-providers-card", padding=20 if wide else 16
        )

    footnote = cards.footnote(screen.footnote, tokens=tokens)
    if wide:
        # Equal heights per row: the cards of one row share the tallest one's height.
        # The footnote sits below the grid, so it never changes a row's height.
        summary: ft.Control = p.MatchedRow(
            cards=cards.kpi_cards(screen.summary, tokens=tokens),
            expands=[1, 1, 1],
        )
        top = p.MatchedRow(cards=[chart_card, plan_card], expands=[155, 100])
        bottom = p.MatchedRow(
            cards=[spaces_card, providers_card],
            expands=[155, 100],
        )
        caveat_row: list[ft.Control] = (
            [cards.caveat_line(screen.caveat, tokens=tokens)] if screen.caveat else []
        )
        return [summary, *caveat_row, top, *empty_note, bottom, footnote]

    controls: list[ft.Control] = []
    if width == sm.WIDTH_MEDIUM:
        controls.append(cards.kpi_row(screen.summary, tokens=tokens, wide=False))
    else:
        controls.append(cards.summary_card(screen.summary, tokens=tokens))
    if screen.caveat:
        controls.append(cards.caveat_line(screen.caveat, tokens=tokens))
    if screen.attention.items:
        controls.append(
            p.card(
                _attention_block(screen, tokens=tokens, on_action=on_action, on_more=on_more),
                tokens=tokens,
                key="usage-attention-card",
                padding=16,
            )
        )
    controls.append(chart_card)
    controls.extend(empty_note)
    if plan_children:
        controls.append(plan_card)
    if spaces_card is not None:
        controls.append(spaces_card)
    if providers_card is not None:
        controls.append(providers_card)
    controls.append(footnote)
    return controls


def _header(
    screen: sm.ScreenModel,
    *,
    tokens: UsageColors,
    wide: bool,
    refresh: ft.Control,
    controls: list[ft.Control],
) -> list[ft.Control]:
    meta_color = tokens.warn if screen.meta_tone == "warn" else tokens.text_muted
    title = ft.Column(
        spacing=2,
        tight=True,
        expand=True,
        controls=[
            ScreenHeader(
                ui(
                    "Usage",
                    color=tokens.text,
                    size=30 if not wide else 32,
                    weight=800,
                    key="usage-title",
                )
            ),
            ui(
                screen.meta, color=meta_color, size=13, weight=600, max_lines=None, key="usage-meta"
            ),
        ],
    )
    if wide:
        return [
            ft.Row(
                controls=[title, *controls, refresh],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
        ]
    return [
        ft.Row(
            controls=[title, refresh], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER
        )
    ]


@ft.component
def UsagePanel(
    *,
    fetch: UsageFetch | None,
    identity: Hashable,
    host_id: str,
    active: bool,
    dark: bool,
    wide: bool,
    width: float | None = None,
    reduce_motion: bool = False,
) -> Any:
    """The Usage page: header, period, pickers, then the page body for the screen's state."""
    tokens = usage_palette(dark)
    period, set_period = ft.use_state("7d")
    space, set_space = ft.use_state(ALL_SPACES)
    provider, set_provider = ft.use_state(ALL_KINDS)
    dialog, set_dialog = ft.use_state("")
    view, set_view = ft.use_state(
        "usage"
    )  # "usage", or "providers" (pushed on a phone, a dialog wide)
    measured, set_measured = ft.use_state(width)
    pins: tuple[str, ...]
    set_pins_state: Any
    pins, set_pins_state = ft.use_state(())
    pins_store: Any = ft.use_ref(None)
    pin_id = pin_store.pin_key(identity, host_id)

    def load_pins() -> None:
        # Pins are per device, per account and host. Without storage they live in memory only.
        if pins_store.current is None:
            try:
                pins_store.current = ft.SharedPreferences()
            except Exception:  # noqa: BLE001 - no page services (tests)
                pins_store.current = False
        store = pins_store.current
        if not store:
            return

        async def fetch_pins() -> None:
            set_pins_state(await pin_store.load(store.get, pin_id))

        asyncio.create_task(fetch_pins())

    ft.use_effect(load_pins, dependencies=[pin_id])

    def set_pins(value: tuple[str, ...]) -> None:
        set_pins_state(value)
        store = pins_store.current
        if not store:
            return

        async def persist() -> None:
            await pin_store.save(store.set, pin_id, value)

        asyncio.create_task(persist())

    # The on-demand limits read: this device's view of the reads it started, per tool.
    local_reads: dict[tuple[Hashable, str, str], sm.RefreshLocal]
    set_local_reads: Any
    local_reads, set_local_reads = ft.use_state({})
    read_tasks: Any = ft.use_ref(None)

    host_ref: Any = ft.use_ref(host_id)
    if host_ref.current != host_id:
        host_ref.current = host_id
        if space != ALL_SPACES:
            set_space(ALL_SPACES)  # a space belongs to one host: never carry it across
        if provider != ALL_KINDS:
            set_provider(ALL_KINDS)

    feed_state, request_refresh, refreshing = use_usage_feed(
        fetch=fetch,
        identity=identity,
        host_id=host_id,
        days=PERIOD_DAYS[period],
        space_id=_server_space(space),
        active=active,
        hour=period == "today",
    )
    width_cls = _width_class(measured, wide)
    flt = _filter(period, space, provider)
    state_key = screen_state(feed_state)
    page = (
        feed_state.page
        if state_key in (presentation_usage.STATE_READY, presentation_usage.STATE_OFFLINE)
        else None
    )
    reads = {
        kind: local
        for (who, host, kind), local in local_reads.items()
        if who == identity and host == host_id
    }
    refresher: Any = getattr(fetch, "refresh_limits", None)

    def put_read(kind: str, value: sm.RefreshLocal) -> None:
        set_local_reads(lambda cur: {**cur, (identity, host_id, kind): value})

    def settle_reads() -> None:
        # A page that arrived settles every read still running (new reading, finished, timed out).
        if page is None or not reads:
            return
        kinds = {entry.kind: entry for entry in page.kinds}
        now = usage_hooks.now_ms()
        changed: dict[str, sm.RefreshLocal] = {}
        for kind, local in reads.items():
            if local.phase != sm.REFRESH_RUNNING:
                continue
            entry = kinds.get(kind)
            reading = s.latest_limit(page, kind)
            settled = sm.settle_refresh(
                local,
                entry.refresh if entry is not None else None,
                reading.observed_ms if reading is not None else None,
                now,
            )
            if settled != local:
                changed[kind] = settled
        for kind, value in changed.items():
            put_read(kind, value)

    ft.use_effect(
        settle_reads,
        dependencies=[
            feed_state.observed_ms,
            tuple(sorted((kind, local.phase) for kind, local in reads.items())),
        ],
    )

    def stop_reads() -> None:
        for task in read_tasks.current or ():
            if not task.done():
                task.cancel()
        read_tasks.current = None

    # Polls end with the view, and with a host or account change (their reads are keyed apart).
    ft.use_effect(lambda: None, dependencies=[identity, host_id], cleanup=stop_reads)

    def press_refresh(kind: str) -> None:
        """Ask the companion to read the tool's limits, then poll the report (2, 4, 8 s ... up
        to about 40 s) until the reading changes or the read ends.
        """
        current = reads.get(kind)
        if refresher is None or not host_id or (current and current.phase == sm.REFRESH_RUNNING):
            return
        reading = s.latest_limit(page, kind) if page is not None else None
        started = usage_hooks.now_ms()
        base = reading.observed_ms if reading is not None else None
        put_read(kind, sm.RefreshLocal(sm.REFRESH_RUNNING, at_ms=started, base_ms=base))

        async def drive() -> None:
            outcome = await refresher(host_id, kind)
            if outcome.attempt_id is not None:
                slot = (identity, host_id, kind)

                def tag(cur: dict[Any, sm.RefreshLocal]) -> dict[Any, sm.RefreshLocal]:
                    mine = cur.get(slot)
                    if mine is None or mine.phase != sm.REFRESH_RUNNING or mine.at_ms != started:
                        return cur
                    return {**cur, slot: replace(mine, attempt_id=outcome.attempt_id)}

                set_local_reads(tag)
            if outcome.state == "cooldown":
                wait_ms = (outcome.retry_after_s or 60) * 1000
                put_read(
                    kind,
                    sm.RefreshLocal(
                        sm.REFRESH_COOLDOWN,
                        until_ms=usage_hooks.now_ms() + wait_ms,
                        at_ms=usage_hooks.now_ms(),
                    ),
                )
                request_refresh()
                return
            if outcome.state not in ("started", "in_progress"):
                error = "not_configured" if outcome.state == "not_configured" else outcome.code
                put_read(
                    kind,
                    sm.RefreshLocal(sm.REFRESH_FAILED, error=error, at_ms=usage_hooks.now_ms()),
                )
                return
            for delay in sm.REFRESH_POLL_SECONDS:
                await asyncio.sleep(delay)
                request_refresh()

        task = asyncio.create_task(drive())
        read_tasks.current = [*(read_tasks.current or []), task]

    # The host's zone outlives a page: while a new period loads, the meta line keeps the host's
    # clock from the last page of this same host (never the device's or UTC).
    zone_ref: Any = ft.use_ref(("", None))
    if zone_ref.current[0] != (identity, host_id):
        zone_ref.current = ((identity, host_id), None)
    if page is not None:
        zone_ref.current = ((identity, host_id), page.timezone)
    memo = ft.use_memo(PageMemo, dependencies=[identity, host_id])
    memo.bind(page)
    screen = sm.build(
        page,
        state=state_key,
        flt=flt,
        now_ms=usage_hooks.now_ms(),
        width=width_cls,
        updating=(refreshing or feed_state.status == STATUS_LOADING) and page is not None,
        inventory=feed_state.inventory,
        timezone=zone_ref.current[1],
        refresh=reads,
        memo=memo,
    )
    # A dialog never shows zero for a value the page did not supply: unknown is "—".
    all_value = screen.summary.tokens if screen.summary is not None else "—"

    readable = {row.refresh.kind for row in screen.plan if row.refresh is not None}

    def refresh_kind(kind: str) -> None:
        """A tool the Mac can read again gets a new reading; for the rest the page reloads."""
        if kind in readable:
            press_refresh(kind)
        else:
            request_refresh()

    def on_action(kind: str, key: str) -> Callable[[Any], None]:
        if kind == "refresh":
            return lambda _e: refresh_kind(key)
        return lambda _e: set_dialog(f"setup:{key}")

    def on_see_spaces(_e: Any) -> None:
        set_dialog("space")

    def on_more(_e: Any) -> None:
        set_view("providers")  # "+k more" and "See all" open the Providers screen

    def open_providers(_e: Any) -> None:
        set_view("providers")

    def on_resize(event: Any) -> None:
        if sm.width_class(event.width) != _width_class(measured, wide):
            set_measured(event.width)

    refresh = p.refresh_button(
        tokens=tokens,
        on_click=lambda _e: request_refresh(),
        disabled=refreshing or fetch is None,
    )
    space_picker = p.picker(
        "Space",
        screen.space_value,
        tokens=tokens,
        key="usage-space-picker",
        active=screen.space_active,
        on_click=lambda _e: set_dialog("space"),
        reset=lambda _e: set_space(ALL_SPACES),
    )
    provider_picker = p.picker(
        "Provider",
        screen.provider_value,
        tokens=tokens,
        key="usage-provider-picker",
        active=screen.provider_active,
        on_click=open_providers,
        reset=lambda _e: set_provider(ALL_KINDS),
    )
    period_track = p.segmented(
        screen.period_options,
        tokens=tokens,
        on_select=lambda value: lambda _e: set_period(value),
        fixed_width=320 if width_cls == sm.WIDTH_WIDE else None,
    )
    picker_row: ft.Control = (
        ft.Row(controls=[space_picker, provider_picker], spacing=10)
        if width_cls != sm.WIDTH_WIDE
        else ft.Row(controls=[space_picker, provider_picker], spacing=10, width=520)
    )
    wide_controls = [period_track, picker_row] if width_cls == sm.WIDTH_WIDE else []
    header = _header(
        screen,
        tokens=tokens,
        wide=width_cls == sm.WIDTH_WIDE,
        refresh=refresh,
        controls=wide_controls,
    )
    stacked: list[ft.Control] = []
    if width_cls != sm.WIDTH_WIDE:
        stacked.append(period_track)
        stacked.append(picker_row)

    commands = {entry.kind: entry for entry in screen.commands}
    setup_kind = dialog.split(":", 1)[1] if dialog.startswith("setup:") else ""
    setup_outdated = setup_kind in commands and commands[setup_kind].outdated
    setup_lines = list(commands[setup_kind].lines) if setup_kind in commands else []
    setup_note = (
        "RelayDeck was updated and its limits hook needs refreshing. Run the command on the "
        "Mac. RelayDeck does not change the Mac from this screen."
        if setup_outdated
        else "Run the command on the Mac. RelayDeck does not change the Mac from this screen."
    )
    setup_doctor = None
    if setup_kind and not setup_lines:
        setup_doctor = (
            "The server did not report this source's format, so the exact command cannot be shown "
            "here. On the Mac, run: relaydeck usage doctor"
        )

    def done_space(chosen: str) -> None:
        set_space(chosen)  # "" for all spaces, a space id, or Not linked's local id
        set_dialog("")

    def choose_provider(kind: str) -> None:
        set_provider(kind)  # a row on the Providers screen filters Usage to that CLI
        set_view("usage")

    def provider_action(kind: str, action: str) -> None:
        if action == "refresh":
            refresh_kind(kind)
        else:
            set_dialog(f"setup:{kind}")

    body = _body(
        screen,
        tokens=tokens,
        width=width_cls,
        on_action=on_action,
        on_see_spaces=on_see_spaces,
        on_more=on_more,
        on_manage=open_providers,
        plot_width=_plot_width(measured, width_cls),
        reduce_motion=reduce_motion,
        on_refresh_limits=press_refresh if refresher is not None else None,
    )
    # The gutter (20 on a phone, 36 on a tablet) sits outside the measured column.
    # The width class is measured on the outer frame, so the page's own viewport decides the layout.
    gutter = 36 if width_cls == sm.WIDTH_WIDE else 20
    wide_layout = width_cls == sm.WIDTH_WIDE
    space_sheet = SpaceSheet(
        open=dialog == "space",
        rows=screen.space_rows,
        selected=space,
        pins=pins,
        all_value=all_value,
        tokens=tokens,
        wide=wide_layout,
        on_done=done_space,
        on_cancel=lambda: set_dialog(""),
        on_pins=set_pins,
    )
    providers_view = ProvidersView(
        lines=screen.connected,
        selected_kind=provider,
        tokens=tokens,
        on_select=choose_provider,
        on_action=provider_action,
    )
    command = dialogs.CommandDialog(
        open=bool(setup_kind),
        title="RelayDeck was updated \u2014 refresh the limits hook"
        if setup_outdated
        else "Set up the limits hook",
        lines=setup_lines,
        note=setup_note,
        doctor=setup_doctor,
        tokens=tokens,
        on_close=lambda: set_dialog(""),
    )
    phone_providers = view == "providers" and not wide_layout
    if phone_providers:
        # The phone pushes the Providers screen inside the Usage tab, with "‹ Usage" back.
        content: list[ft.Control] = [
            ft.Row(
                controls=[
                    Button(
                        variant="text",
                        key="usage-providers-back",
                        content=ui("‹ Usage", color=tokens.link, size=15, weight=800, max_lines=1),
                        on_click=lambda _e: set_view("usage"),
                    ),
                ],
            ),
            ScreenHeader(
                ui("Providers", color=tokens.text, size=30, weight=800, key="usage-providers-title")
            ),
            providers_view,
        ]
    else:
        content = [*header, *stacked, *body]
    return ft.Container(
        key="usage-panel-frame",
        expand=True,
        padding=ft.Padding.symmetric(horizontal=gutter, vertical=16),
        on_size_change=on_resize,
        content=ft.Column(
            key="usage-panel",
            expand=True,
            scroll=ft.ScrollMode.AUTO,
            spacing=16,
            controls=[
                *content,
                space_sheet,
                ProvidersDialog(
                    open=view == "providers" and wide_layout,
                    lines=screen.connected,
                    selected_kind=provider,
                    tokens=tokens,
                    on_select=choose_provider,
                    on_action=provider_action,
                    on_close=lambda: set_view("usage"),
                ),
                command,
            ],
        ),
    )
