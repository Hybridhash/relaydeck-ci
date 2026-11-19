"""Shared client definitions keep one identity across their consumers."""

from __future__ import annotations

import importlib

import relaydeck.client.platform.transport.sse as sse_transport
import relaydeck.client.platform.transport.transport as transport_transport
import relaydeck.client.ports.transport as transport_ports
from tests.support.client.defaults import (
    _open_hold_released_unless_opted_in as _open_hold_released_unless_opted_in,
)
from tests.support.client.defaults import (
    _watch_first_unless_opted_in as _watch_first_unless_opted_in,
)


def test_transport_types_have_one_identity() -> None:
    from relaydeck.client.platform.transport import transport
    from relaydeck.client.ports import transport as ports

    for name in ("TransportError", "CommandNotAccepted", "GatewayTransport", "TerminalSocket"):
        assert getattr(transport, name) is getattr(ports, name)
    assert transport_transport.SseFrame is transport_ports.SseFrame is ports.SseFrame
    assert transport_transport.HttpxGatewayTransport.__bases__ == (ports.GatewayTransport,)
    assert transport_transport.WebsocketTerminalSocket.__bases__ == (ports.TerminalSocket,)
    assert sse_transport.SseParser().feed(b"event: update\ndata: value\n\n") == [
        ports.SseFrame("update", "value")
    ]


def test_command_errors_have_one_identity() -> None:
    from relaydeck.client.app.hooks import creation, support
    from relaydeck.client.features.commands import observation, recovery
    from relaydeck.client.features.composer import model as domain
    from relaydeck.client.features.composer import reconcile
    from relaydeck.client.platform.transport import transport
    from relaydeck.client.ports import commands
    from relaydeck.client.ports import transport as ports

    assert (
        observation.CommandOutcomeUnknown
        is support.CommandOutcomeUnknown
        is commands.CommandOutcomeUnknown
    )
    assert observation.CommandRefused is creation.CommandRefused is commands.CommandRefused
    assert (
        observation.CommandRecoveryUnavailable
        is reconcile.CommandRecoveryUnavailable
        is transport.CommandRecoveryUnavailable
        is commands.CommandRecoveryUnavailable
    )
    assert recovery.RecoveryError is commands.RecoveryError
    assert recovery.UnconfirmedEarlier is domain.UnconfirmedEarlier is commands.UnconfirmedEarlier
    assert issubclass(commands.RecoveryError, ports.TransportError)
    assert issubclass(commands.UnconfirmedEarlier, commands.RecoveryError)
    assert issubclass(commands.CommandRecoveryUnavailable, commands.CommandOutcomeUnknown)


def test_input_values_have_one_identity() -> None:
    from relaydeck.client.features.composer import model as domain
    from relaydeck.client.features.terminal import input_modes, keys
    from relaydeck.client.models.input import key_bar, modes
    from relaydeck.client.models.input import keys as values

    for name in ("INPUT_MODE_COMPOSER", "INPUT_MODE_TOOLS_KEYS"):
        assert getattr(domain, name) is getattr(modes, name)
    for name in (
        "WORKFLOW_AGENT_COMPOSER",
        "WORKFLOW_AGENT_DIRECT",
        "WORKFLOW_SHELL",
        "KEYBOARD_TEXT",
        "KEYBOARD_KEYS",
    ):
        assert getattr(input_modes, name) is getattr(modes, name)
    for name in ("MOD_CTRL", "MOD_ALT", "MOD_SHIFT", "MODIFIERS", "SHIFT_TAB", "SHIFT_ENTER"):
        assert getattr(keys, name) is getattr(values, name)
    assert key_bar.MODIFIERS is values.MODIFIERS


def test_root_exports_and_logger_keep_their_identity() -> None:
    from relaydeck.client.app import root
    from relaydeck.client.app.hooks import support
    from relaydeck.client.app.shell import navigation

    assert root.AppShell is navigation.AppShell
    assert root.new_command_id is support.new_command_id
    assert root.selection_host is support.selection_host
    assert root.logger.name == "relaydeck.client.app"
    assert root.logger is support.logger
    assert vars(importlib.import_module("relaydeck.client.app")).get("Root") is None


def test_diagnostic_names_and_command_builder_are_shared() -> None:
    from relaydeck.client.features.create import commands
    from relaydeck.client.models.commands import session_close
    from relaydeck.client.platform.diagnostics import terminal
    from relaydeck.client.platform.flet import events

    assert commands.envelope is session_close.envelope
    assert terminal.diagnostics_logger().name == "relaydeck.client.typing_split"
    assert events.logger.name == "relaydeck.client.platform.flet.events"


def test_picture_constructor_is_injected_without_changing_adapter_semantics() -> None:
    import httpx

    from relaydeck.client.features.terminal.pictures import PictureApi
    from relaydeck.client.platform.transport.shell_ops import HttpxShellGatewayOps

    async def token() -> str:
        return "synthetic"

    ops = HttpxShellGatewayOps(
        "https://gateway.test", token, httpx.AsyncClient(), picture_api_factory=PictureApi
    )
    first, second = ops.picture_api, ops.picture_api
    assert isinstance(first, PictureApi) and isinstance(second, PictureApi)
    assert first is not second and first.ops is second.ops is ops
    assert first.native is False
    ops.native_picture_upload = True
    assert ops.picture_api.native is True
