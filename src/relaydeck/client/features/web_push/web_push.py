"""Settings > Notifications for the browser app: this device's state, its choice, and Turn off.
The browser turns notifications on from the banner (a user tap and the browser's permission).
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any

import flet as ft

from relaydeck.client.design_system import tokens as T
from relaydeck.client.design_system.buttons import Button
from relaydeck.client.design_system.fonts import FAMILY_INTERFACE
from relaydeck.client.design_system.primitives import text_style
from relaydeck.client.design_system.theme import palette
from relaydeck.client.features.web_push.source import WebPushSource
from relaydeck.client.ports.transport import TransportError

ON_TEXT = "Notifications are on for this device"
OFF_TEXT = (
    "Notifications are off for this device. Turn them on with the bar at the top of RelayDeck. "
    "On iPhone and iPad, first add RelayDeck to the Home Screen (Share → Add to Home Screen) "
    "and open it from there. If you closed the bar with ×, changing the choice below or "
    "reopening RelayDeck brings it back."
)
TURN_OFF_FAILED = "Could not turn notifications off. Try again."

# The choices a device can make, with the category names the companion stores (blocked, done).
CHOICES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("all", ("blocked", "done"), "All: when an agent needs attention or finishes"),
    ("blocked", ("blocked",), "Only when an agent needs attention"),
    ("done", ("done",), "Only when an agent finishes"),
    ("none", (), "None: stays subscribed, sends nothing"),
)
DEFAULT_CHOICE = "all"
STATUS_TEXT = {
    "loading": "Loading notification choice…",
    "ready": "",
    "saving": "Saving…",
    "saved": "Saved",
    "error": "Could not save. Try again.",
    "offline": "Offline: notification choices can be changed when you reconnect.",
}


def status_line(subscribed: bool) -> str:
    return ON_TEXT if subscribed else OFF_TEXT


def choice_for(categories: Any) -> str | None:
    """The UI choice for a stored set of categories, or None when no choice matches it."""
    wanted = frozenset(categories)
    for key, cats, _label in CHOICES:
        if frozenset(cats) == wanted:
            return key
    return None


def categories_for(choice: str) -> list[str]:
    for key, cats, _label in CHOICES:
        if key == choice:
            return list(cats)
    raise ValueError("unknown notification choice")


@dataclass(frozen=True)
class PrefsState:
    choice: str | None = None
    status: str = "loading"  # loading, ready, saving, saved, error or offline

    @property
    def enabled(self) -> bool:
        """The radio buttons work only when the device's choice is known and nothing is pending."""
        return self.status in ("ready", "saved", "error")

    @property
    def text(self) -> str:
        return STATUS_TEXT[self.status]


def is_offline(exc: BaseException) -> bool:
    """A network failure, as the transport reports it (the request never reached the companion)."""
    return isinstance(exc, TransportError) and exc.code == "host_offline"


async def load_choice(transport: WebPushSource) -> PrefsState:
    try:
        categories = await transport.web_push_preferences()
    except Exception as exc:  # noqa: BLE001 - shown as a state, never as a raw error
        return PrefsState(None, "offline" if is_offline(exc) else "error")
    return PrefsState(choice_for(categories), "ready")


async def save_choice(transport: WebPushSource, current: PrefsState, choice: str) -> PrefsState:
    categories = categories_for(choice)  # an unknown choice is a bug here, never sent
    try:
        saved = await transport.set_web_push_preferences(categories)
    except Exception as exc:  # noqa: BLE001 - keep the last known choice on failure
        return PrefsState(current.choice, "offline" if is_offline(exc) else "error")
    return PrefsState(choice_for(saved), "saved")


@ft.component
def WebPushPanel(transport: WebPushSource, dark: bool = True) -> Any:
    loaded: dict[str, bool] | None = None  # None while loading; then web_push_state()
    state, set_state = ft.use_state(loaded)
    prefs, set_prefs = ft.use_state(PrefsState())
    busy, set_busy = ft.use_state(False)
    notice, set_notice = ft.use_state("")

    owner: Any = ft.use_ref(None)
    tasks: Any = ft.use_ref(set())
    active: Any = ft.use_ref(None)
    initial_owner: object | None = None
    loaded_owner, set_loaded_owner = ft.use_state(initial_owner)
    if owner.current is None or owner.current[0] is not transport:
        owner.current = (transport, object())
    token = owner.current[1]

    def current() -> bool:
        return owner.current is not None and owner.current[1] is token

    def launch(operation: Coroutine[Any, Any, None]) -> None:
        if not current():
            operation.close()
            return
        task = asyncio.create_task(operation)
        tasks.current.add(task)
        task.add_done_callback(tasks.current.discard)

    def retire() -> None:
        installed = active.current
        active.current = None
        if installed is not None and owner.current is installed:
            owner.current = None
        for task in tuple(tasks.current):
            task.cancel()
        tasks.current.clear()

    def load() -> None:
        active.current = owner.current
        set_loaded_owner(None)
        set_state(None)
        set_prefs(PrefsState())
        set_busy(False)
        set_notice("")

        async def run() -> None:
            try:
                doc = await transport.web_push_state()
            except Exception:  # noqa: BLE001 - no route, no web push, or no network: show nothing
                doc = {"enabled": False, "subscribed": False}
            if not current():
                return
            set_state(doc)
            set_loaded_owner(token)
            if doc.get("enabled"):
                choice = await load_choice(transport)
                if current():
                    set_prefs(choice)

        launch(run())

    ft.use_effect(load, dependencies=[token], cleanup=retire)

    def change_choice(event: Any) -> None:
        choice = str(event.control.value or "")
        if (
            not current()
            or choice not in {key for key, _cats, _label in CHOICES}
            or not prefs.enabled
        ):
            return
        set_prefs(PrefsState(prefs.choice, "saving"))

        async def run() -> None:
            result = await save_choice(transport, prefs, choice)
            if current():
                set_prefs(result)

        launch(run())

    def turn_off(_event: Any = None) -> None:
        if not current():
            return

        async def run() -> None:
            set_busy(True)
            try:
                await transport.web_push_disable()
                if not current():
                    return
                set_state({"enabled": True, "subscribed": False})
                set_notice("")
            except Exception:  # noqa: BLE001 - the server state is authoritative; say so simply
                if current():
                    set_notice(TURN_OFF_FAILED)
            finally:
                if current():
                    set_busy(False)

        launch(run())

    if loaded_owner is not token or state is None or not state.get("enabled"):
        return ft.Container(key="web-push-settings-empty")
    subscribed = bool(state.get("subscribed"))
    colors = palette(dark)
    radios: list[ft.Control] = [
        ft.Radio(value=key, label=label, key=f"notify-choice-{key}")
        for key, _cats, label in CHOICES
    ]
    return ft.Column(
        key="web-push-settings",
        spacing=8,
        controls=[
            ft.Text(
                "Notifications",
                color=colors.text,
                style=text_style(FAMILY_INTERFACE, T.TYPE_CARD_LINE_STRONG, T.TEXT),
            ),
            ft.Text(
                status_line(subscribed),
                key="notifications-status",
                color=colors.muted,
                style=text_style(FAMILY_INTERFACE, T.TYPE_META, T.TEXT_MUTED),
            ),
            ft.RadioGroup(
                key="notifications-choice",
                value=prefs.choice or "",
                disabled=not prefs.enabled,
                on_change=change_choice,
                content=ft.Column(spacing=4, controls=radios),
            ),
            ft.Text(
                prefs.text,
                key="notifications-choice-status",
                color=colors.muted,
                visible=bool(prefs.text),
            ),
            *(
                [
                    Button(
                        "Turn off",
                        key="notifications-turn-off",
                        disabled=busy,
                        on_click=turn_off,
                    )
                ]
                if subscribed
                else []
            ),
            *([ft.Text(notice, key="notifications-notice")] if notice else []),
        ],
    )


__all__ = [
    "CHOICES",
    "DEFAULT_CHOICE",
    "OFF_TEXT",
    "ON_TEXT",
    "PrefsState",
    "WebPushPanel",
    "categories_for",
    "choice_for",
    "load_choice",
    "save_choice",
    "status_line",
]
