"""Actual notices and empty panes with their existing actions and layout."""

from types import SimpleNamespace
from typing import Any

import flet as ft

from relaydeck.client.design_system.controls import NoticePanel, PillButton
from relaydeck.client.design_system.notices import message_box
from relaydeck.client.features.pairing.api import ConnectState
from relaydeck.client.models.inventory_selectors import BannerModel
from relaydeck.client.screens.changes.parts.commit_view import commit_lost
from relaydeck.client.screens.changes.parts.diff_chrome import _message
from relaydeck.client.screens.console.parts.lists import ConnectionBanner
from relaydeck.client.widgets.connect.connect import ConnectPanel
from relaydeck.client.widgets.terminal.window import DirectInputStrip, TakeoverPanel
from tests.support.host_harness import render_component

CASES = (
    "notice-phone",
    "notice-wide",
    "notice-empty-message",
    "message-plain",
    "message-action",
    "diff-plain",
    "diff-load",
    "diff-extra",
    "commit-pane",
    "commit-inline",
    "banner-passive",
    "banner-active",
    "pairing-note",
    "direct-strip",
    "takeover-wide",
    "takeover-phone",
    "takeover-busy",
    "takeover-quick",
)


def noop(*args: Any) -> None:
    pass


def notice_view(case: str) -> Any:
    if case.startswith("notice-"):
        return NoticePanel(
            title="Example title",
            message="" if case == "notice-empty-message" else "Example message",
            actions=[PillButton("Continue", noop, key="example-action")],
            key="notice",
            phone=case == "notice-phone",
        )
    if case.startswith("message-"):
        return message_box(
            "Example message",
            [PillButton("Continue", noop, key="example-action")]
            if case == "message-action"
            else [],
            key="message",
        )
    if case.startswith("diff-"):
        return _message(
            "Example message",
            noop if case == "diff-load" else None,
            [ft.Text("Example detail")] if case == "diff-extra" else [],
        )
    if case.startswith("commit-"):
        return commit_lost(SimpleNamespace(refresh=noop), pane=case == "commit-pane")
    if case.startswith("banner-"):
        return ConnectionBanner(
            BannerModel(
                "info", "Example connection status", "Retry" if case == "banner-active" else ""
            ),
            noop,
        )
    if case == "pairing-note":
        return ConnectPanel(
            ConnectState(),
            on_endpoint=noop,
            on_host_id=noop,
            on_label=noop,
            on_begin=noop,
            on_cancel=noop,
            on_retry=noop,
        )
    if case == "direct-strip":
        return DirectInputStrip("Example input notice")
    assert case.startswith("takeover-")
    return TakeoverPanel(
        title="Example ownership",
        message="Example consequences",
        on_take_over=noop,
        on_keep_watching=noop,
        phone=case == "takeover-phone",
        busy=case == "takeover-busy",
        confirm=case != "takeover-quick",
    )


async def render_notice(case: str) -> Any:
    return await render_component(notice_view, case)
