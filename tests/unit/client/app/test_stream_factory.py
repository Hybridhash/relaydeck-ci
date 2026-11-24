"""The injected stream retains its construction point, callback wiring and ownership."""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from relaydeck.client.features.terminal.controller import TerminalController
from relaydeck.client.features.terminal.stream import TerminalByteStream
from relaydeck.client.platform.transport.terminal_stream import TerminalStreamClient
from relaydeck.client.ports.terminal import LeaseInfo
from relaydeck.client.ports.transport import TerminalSocket
from relaydeck.lease_diagnostics import LeaseTrace
from tests.support.scripted_sessions import make_scripted_sessions


@pytest.mark.parametrize("mode", ["view", "control"])
@pytest.mark.parametrize("diagnostics", [None, LeaseTrace("session", "terminal")])
def test_stream_construction_keeps_initial_state_and_exact_callbacks(
    mode: str, diagnostics: LeaseTrace | None
) -> None:
    events: list[str] = []
    clients: list[TerminalStreamClient] = []
    owners: list[TerminalController] = []
    failures: list[tuple[int, int]] = []

    def socket(ticket: str) -> TerminalSocket:
        raise AssertionError("construction must not open a socket")

    def factory(
        *,
        socket_factory: Callable[[str], TerminalSocket],
        on_output: Callable[[int, bytes], None],
        on_control: Callable[[dict[str, Any]], None],
        on_gap: Callable[[str], None],
        on_reset: Callable[[str], None],
        on_input_failure: Callable[[int, int], None],
        diagnostics: LeaseTrace | None,
        on_baseline: Callable[[dict[str, Any]], Awaitable[bool]],
    ) -> TerminalStreamClient:
        owner = on_output.__self__
        assert isinstance(owner, TerminalController)
        assert owner.state.lease_mode == mode
        assert isinstance(owner._stream, TerminalByteStream)
        assert owner._started is False
        assert not hasattr(owner, "_client")
        owners.append(owner)
        events.append("construct")
        client = TerminalStreamClient(
            socket_factory=socket_factory,
            diagnostics=diagnostics,
            on_output=on_output,
            on_control=on_control,
            on_gap=on_gap,
            on_reset=on_reset,
            on_input_failure=on_input_failure,
            on_baseline=on_baseline,
        )
        clients.append(client)
        return client

    controller = TerminalController(
        socket,
        "lease-7",
        7,
        mode,
        lambda data: None,
        stream_factory=factory,
        diagnostics=diagnostics,
        on_input_failure=lambda generation, sequence: failures.append((generation, sequence)),
    )
    assert owners == [controller] and events == ["construct"]
    assert len(clients) == 1
    client = clients[0]
    assert type(client) is TerminalStreamClient
    assert controller._client is client
    assert client.socket_factory is socket and client.diagnostics is diagnostics
    assert client.on_output == controller._handle_output
    assert client.on_control == controller._handle_control
    assert client.on_gap == controller._handle_gap
    assert client.on_reset == controller._handle_reset
    assert client.on_baseline == controller._handle_baseline
    assert client.on_input_failure is not None
    client.on_input_failure(7, 19)
    assert failures == [(7, 19)]


def test_app_factory_threads_the_same_class_to_distinct_session_streams() -> None:
    ports = make_scripted_sessions()
    agent = ports.factory.make_agent(host_id="mac", host_epoch="h1", agent_id="pane", target="pane")
    shell = ports.factory.make_shell(host_id="mac")
    assert ports.streams.opened == []
    controllers = []
    for session in (agent, shell):
        assert session._stream_factory is TerminalStreamClient
        assert session.controller is None
        lease = LeaseInfo(
            lease_id="lease-7",
            attachment_id="attachment",
            host_id="mac",
            terminal_id="pane",
            mode="control",
            generation=7,
            epoch="epoch",
            expires_at="later",
        )
        controller = session._spawn_controller(lease)
        controllers.append(controller)
        assert controller.stream_factory is TerminalStreamClient
        assert type(controller._client) is TerminalStreamClient
        assert controller.gap_recovery is session.gap_recovery
        assert controller._client._socket is None
    assert controllers[0] is not controllers[1]
    assert controllers[0]._client is not controllers[1]._client
    assert ports.streams.opened == []
