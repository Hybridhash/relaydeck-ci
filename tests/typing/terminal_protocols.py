"""Concrete terminal owners satisfy the capabilities their consumers use."""

from relaydeck.client.features.terminal.agent_session import AgentSession
from relaydeck.client.features.terminal.controller import TerminalController
from relaydeck.client.features.terminal.session.base import TerminalSessionBase
from relaydeck.client.features.terminal.session.context import InputSessions, SessionContext
from relaydeck.client.features.terminal.sessions import DetailSessions
from relaydeck.client.features.terminal.transition_port import TransitionSession
from relaydeck.client.platform.transport.terminal_stream import TerminalStreamClient
from relaydeck.client.ports.terminal import KeyWriter
from relaydeck.client.ports.transport import TerminalStream, TerminalStreamFactory


def key_writer(controller: TerminalController) -> KeyWriter:
    return controller


def control_transition(session: AgentSession) -> TransitionSession:
    return session


def session_callbacks(session: TerminalSessionBase) -> SessionContext:
    return session


def input_bundle(value: DetailSessions) -> InputSessions:
    return value


def stream_capabilities(value: TerminalStreamClient) -> TerminalStream:
    return value


def stream_factory() -> TerminalStreamFactory:
    return TerminalStreamClient
