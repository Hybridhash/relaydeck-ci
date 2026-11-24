"""Actual badge and chip callers with their local presentation variants."""

from typing import Any

import flet as ft

from relaydeck.client.design_system import tokens as T
from relaydeck.client.design_system.controls import STATE_LABELS, StatePill
from relaydeck.client.design_system.primitives import StatusPill
from relaydeck.client.design_system.theme import palette
from relaydeck.client.features.changes.api import ChangesModel
from relaydeck.client.features.rename.rename_model import SheetContext, Verdict
from relaydeck.client.features.rename.rename_sheet import SheetActions, SheetProps, _chips
from relaydeck.client.features.terminal.connecting_dots import ConnectingDots
from relaydeck.client.screens.changes.parts.file_list import _BADGES, file_row
from relaydeck.client.screens.changes.parts.graph_rows import ref_badges
from relaydeck.client.screens.changes.parts.header import _chip
from relaydeck.client.screens.settings.parts.server_health_model import RefreshRow
from relaydeck.client.screens.settings.parts.server_health_parts import RefreshPill
from relaydeck.client.screens.usage.parts.primitives import badge
from relaydeck.client.widgets.connect.connect import _step
from relaydeck.client.widgets.hosts.hosts import _host_chip
from relaydeck.client.widgets.sessions.pills import AgentPill, HostChip, SpaceChip
from relaydeck.client.widgets.terminal.agent_switcher import _badge
from relaydeck.client.widgets.usage.cards import console_card
from relaydeck.client.widgets.usage.model import ConsoleCardModel
from relaydeck.contracts.git import FileEntry, GraphCommit, GraphRef
from tests.support.host_harness import render_component

CASES = (
    "window-states",
    "status-pills",
    "identities",
    "host-filters",
    "host-label",
    "working-count",
    "reference-chips",
    "graph-refs",
    "graph-budget",
    "refresh-tones",
    "usage-light",
    "usage-dark",
    "suggestions-light",
    "suggestions-dark",
    "suggestions-disabled",
    "pairing-step",
    "file-statuses",
    "connecting-light",
    "connecting-dark",
    "partial-light",
    "partial-dark",
)


def noop(*args: Any) -> None:
    pass


def badge_view(case: str) -> ft.Column:
    if case == "pairing-step":
        controls = [_step("1", "Example step", ft.Text("Example instructions"))]
    elif case == "file-statuses":
        controls = [
            file_row(
                index,
                FileEntry(
                    file_id=f"file-{index}",
                    path="example.py",
                    status=status,
                    staged=False,
                    unstaged=True,
                    additions=1,
                    deletions=2,
                    generated=False,
                    withheld=False,
                    rename_limited=False,
                    renderable=True,
                ),
                False,
                False,
                noop,
                "file",
            )
            for index, status in enumerate(_BADGES)
        ]
    elif case.startswith("connecting-"):
        controls = [ConnectingDots(palette(case.endswith("dark")), True, reduce_motion=True)]
    elif case.startswith("partial-"):
        controls = [
            console_card(
                ConsoleCardModel(True, False, "Example usage", "", "", False, partial=True),
                tokens=T.usage_palette(case.endswith("dark")),
                on_open=noop,
            )
        ]
    elif case == "window-states":
        controls = [
            StatePill(state, key=f"state-{state}-{compact}", compact=compact)
            for state in (*STATE_LABELS, "custom")
            for compact in (False, True)
        ]
    elif case == "status-pills":
        controls = [
            StatusPill(state, inverted=inverted, key=f"status-{state}-{inverted}")
            for state in T.STATUS_LABELS
            for inverted in (False, True)
        ] + [StatusPill("invalid", label="Custom status")]
    elif case == "identities":
        controls = [
            builder("Example identity", T.PRIMARY_TEXT, key=f"identity-{index}-{inverted}", ink=ink)
            for index, builder in enumerate((AgentPill, SpaceChip))
            for inverted, ink in ((False, None), (True, T.BLOCKED_TEXT))
        ]
    elif case == "host-filters":
        controls = [
            HostChip(
                "host-a",
                "Example host",
                selected,
                noop,
                compact=compact,
                key=f"filter-{selected}-{compact}",
            )
            for selected in (False, True)
            for compact in (False, True)
        ]
    elif case == "host-label":
        controls = [_host_chip("Example connection")]
    elif case == "working-count":
        controls = [_badge(3)]
    elif case == "reference-chips":
        controls = [
            _chip("Example reference", key=f"reference-{accent}", accent=accent)
            for accent in (False, True)
        ]
    elif case.startswith("graph-"):
        commit = GraphCommit(
            oid="a" * 40,
            parents=(),
            subject="Example change",
            time_ms=0,
            ahead=True,
            refs=tuple(
                GraphRef(name=name, kind=kind)
                for name, kind in (
                    ("feature/example", "branch"),
                    ("HEAD", "head"),
                    ("main", "base"),
                    ("split", "split"),
                    ("v1", "tag"),
                )
            ),
        )
        controls = [
            ref_badges(commit, ChangesModel(), budget=50 if case == "graph-budget" else None)
        ]
    elif case == "refresh-tones":
        controls = [
            RefreshPill(RefreshRow(tone, "Just now", "Example status", tone))
            for tone in ("ok", "warn", "danger", "neutral")
        ]
    elif case.startswith("usage-"):
        controls = [
            badge("Example label", tokens=T.usage_palette(case.endswith("dark")), tone=tone)
            for tone in ("warn", "danger", "neutral", "count")
        ]
    else:
        assert case.startswith("suggestions-")
        props = SheetProps(
            open=True,
            phone=True,
            dark=case.endswith("dark"),
            value="Example",
            context=SheetContext(
                "Example task", "Example provider", "Example space", "idle", T.TEXT
            ),
            verdict=Verdict("", ""),
            error="",
            status="",
            busy=case.endswith("disabled"),
            uncertain=False,
            can_save=True,
            save_label="Save",
            select_all=False,
            suggestions=("Example suggestion", "Longer example suggestion"),
        )
        controls = _chips(
            props, SheetActions(noop, noop, noop, noop, noop, noop), T.usage_palette(props.dark)
        )
    return ft.Column(controls=controls)


async def render_badges(case: str) -> Any:
    return await render_component(badge_view, case)
