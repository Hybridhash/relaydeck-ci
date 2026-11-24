"""j / k / v on the wide Changes layout: when they act and when they must not."""

from __future__ import annotations

from typing import Any

import pytest

from relaydeck.client.features.composer.shortcut import add_key_listener
from relaydeck.client.screens.changes.parts.review_keys import key_action
from tests.support.host_harness import HostApp, run
from tests.ui.git.gateway import KEY, Gateway
from tests.ui.git.harness import click, mount, small_files, tick, until


async def press(app: HostApp, key: str, **modifiers: bool) -> None:
    event = {"key": key, "ctrl": False, "meta": False, "alt": False, "shift": False, **modifiers}
    await app.session.dispatch_event(app.page._i, "keyboard_event", event)
    await app.settle()


def diffs(gateway: Gateway) -> list[Any]:
    return [call[1] for call in gateway.calls if call[0] == "diff"]


async def open_first(gateway: Gateway, *, wide: bool = True) -> tuple[HostApp, dict[str, Any]]:
    app, control = await mount(gateway, wide=wide)
    await app.wait_for("changes-files")
    await click(app, "changes-file-0")
    await app.wait_for("diff-rows")
    return app, control


def three_files() -> Gateway:
    gateway = Gateway(small_files())
    gateway.files.append(gateway.files[0].__class__("pkg/c.py", gateway.files[0].patch))
    return gateway


def test_j_and_k_move_between_files_and_v_toggles_viewed() -> None:
    gateway = three_files()

    async def scenario() -> None:
        app, _ = await open_first(gateway)
        await press(app, "j")
        await until(lambda: diffs(gateway)[-1] == "rev-1:1")
        await press(app, "J")  # the key label may be upper case
        await until(lambda: diffs(gateway)[-1] == "rev-1:2")
        await press(app, "j")  # the last file: stays
        await press(app, "k")
        await until(lambda: diffs(gateway)[-1] == "rev-1:1")
        await app.wait_for("diff-rows")

        assert app.find("changes-viewed").value is False
        await press(app, "v")
        assert app.find("changes-viewed").value is True
        await press(app, "v")
        assert app.find("changes-viewed").value is False
        await tick(app, "changes-viewed", True)  # the checkbox and the key agree
        await press(app, "v")
        assert app.find("changes-viewed").value is False

    run(scenario())


def test_keys_are_ignored_while_the_file_search_has_focus() -> None:
    gateway = three_files()

    async def scenario() -> None:
        app, _ = await open_first(gateway)
        before = len(diffs(gateway))
        await app.set_props_and_fire("changes-search", {}, "focus")
        for key in "jkv":
            await press(app, key)
        assert len(diffs(gateway)) == before and app.find("changes-viewed").value is False
        await app.set_props_and_fire("changes-search", {}, "blur")
        await press(app, "v")
        assert app.find("changes-viewed").value is True

    run(scenario())


@pytest.mark.parametrize("modifier", ["ctrl", "meta", "alt"])
def test_keys_are_ignored_with_a_modifier_held(modifier: str) -> None:
    gateway = three_files()

    async def scenario() -> None:
        app, _ = await open_first(gateway)
        before = len(diffs(gateway))
        for key in "jkv":
            await press(app, key, **{modifier: True})
        assert len(diffs(gateway)) == before and app.find("changes-viewed").value is False

    run(scenario())


def test_keys_are_ignored_while_a_sheet_is_open() -> None:
    gateway = three_files()

    async def scenario() -> None:
        app, _ = await open_first(gateway)
        await click(app, "changes-scope-branch")
        await app.wait_for("changes-change-base")
        await click(app, "changes-change-base")
        await app.wait_for("changes-base-picker")
        before = len(diffs(gateway))
        await press(app, "j")
        assert len(diffs(gateway)) == before

    run(scenario())


def test_the_phone_layout_does_not_listen() -> None:
    gateway = three_files()

    async def scenario() -> None:
        app, _ = await open_first(gateway, wide=False)
        assert app.page.on_keyboard_event is None
        before = len(diffs(gateway))
        await press(app, "j")
        await press(app, "v")
        assert len(diffs(gateway)) == before and app.find("changes-viewed").value is False

    run(scenario())


def test_rotating_to_the_wide_layout_starts_listening_and_back_stops() -> None:
    gateway = three_files()

    async def scenario() -> None:
        app, control = await open_first(gateway, wide=False)
        control["set_wide"](True)
        await until(lambda: app.page.on_keyboard_event is not None)
        control["set_wide"](False)
        await until(lambda: app.page.on_keyboard_event is None)

    run(scenario())


def test_the_handler_is_released_with_the_screen_and_shared_with_other_listeners() -> None:
    gateway = three_files()

    async def scenario() -> None:
        app, control = await open_first(gateway)
        assert app.page.on_keyboard_event is not None
        control["navigate"]("agents")  # leave the route: the screen unmounts
        await app.wait_for("console")
        assert app.page.on_keyboard_event is None
        before = len(diffs(gateway))
        await press(app, "j")
        assert len(diffs(gateway)) == before

        seen: list[str] = []
        remove = add_key_listener(lambda event: seen.append(event.key))
        control["navigate"]("agents", KEY, section="changes")
        await app.wait_for("changes-files")
        await click(app, "changes-file-0")
        await app.wait_for("diff-rows")
        control["navigate"]("agents")
        await app.wait_for("console")
        assert app.page.on_keyboard_event is not None  # another listener still needs it
        await press(app, "x")
        assert seen == ["x"]
        remove()
        assert app.page.on_keyboard_event is None

    run(scenario())


def test_only_the_review_keys_without_application_modifiers_are_actions() -> None:
    class Event:
        def __init__(self, key: str, **mods: bool) -> None:
            self.key, self.ctrl, self.meta, self.alt = key, False, False, False
            for name, value in mods.items():
                setattr(self, name, value)

    assert [key_action(Event(k)) for k in ("j", "K", "v", "x", "Enter")] == [
        "next",
        "previous",
        "viewed",
        None,
        None,
    ]
    assert key_action(Event("j", ctrl=True)) is None
    assert key_action(Event("j", meta=True)) is None
    assert key_action(Event("j", alt=True)) is None
