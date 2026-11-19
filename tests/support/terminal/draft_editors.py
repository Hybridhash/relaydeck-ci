"""Mounted phone and desktop draft editors over the same prepared session contract."""

from typing import Any

import flet as ft
import msgpack  # type: ignore[import-untyped]
from flet.controls.base_control import BaseControl
from flet.messaging.protocol import MessageAction, configure_encode_object_for_msgpack

from relaydeck.client.features.composer.components import ComposerBox
from relaydeck.client.widgets.terminal.phone_composer import _PhoneComposerField


@ft.component
def DraftView(session: Any, phone: bool = False, enabled: bool = True) -> Any:
    callbacks = session.composer_callbacks()
    if phone:
        return _PhoneComposerField(
            draft=session.store.draft,
            dark=True,
            blocked=False,
            compact=False,
            focusable=enabled,
            send_enabled=session.store.can_send,
            on_draft_change=callbacks["on_draft_change"],
            on_send=callbacks["on_send"],
        )
    return ComposerBox(**session.composer_props(), **callbacks, focusable=enabled, keyboard="hide")


@ft.component
def Boundaries(session: Any, phone: bool, state: dict[str, Any]) -> Any:
    _tick, state["bump"] = ft.use_state(0)
    return DraftView(session, phone, state["enabled"])


def hide_toggle(app: Any) -> Any:
    suffix = app.find("composer-draft").suffix_icon
    items = suffix.controls if isinstance(suffix, ft.Row) else [suffix]
    for item in items:
        content = getattr(item, "content", None)
        if getattr(content, "key", None) == "composer-hide-keyboard":
            return content
    raise KeyError("composer-hide-keyboard")


def assert_disabled_carrier(app: Any, native: Any, start: int) -> None:
    """Check the actual encoded replacement, including its native focus gates."""
    encode = configure_encode_object_for_msgpack(BaseControl)  # type: ignore[no-untyped-call]
    patches = [
        message.body.patch
        for message in app.conn.sent[start:]
        if message.action == MessageAction.PATCH_CONTROL
    ]
    wire = msgpack.unpackb(msgpack.packb(patches, default=encode), raw=False, strict_map_key=False)

    def matches(value: Any) -> bool:
        if isinstance(value, dict):
            if value.get("_i") == native._i:
                return value.get("disabled") is True and value.get("can_request_focus") is False
            return any(matches(item) for item in value.values())
        if isinstance(value, list):
            return any(matches(item) for item in value)
        return False

    assert matches(wire), "Disabled replacement missing from encoded carrier"
