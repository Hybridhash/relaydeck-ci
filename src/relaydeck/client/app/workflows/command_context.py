"""Fresh command context shared by rendering and contextual submission."""

from __future__ import annotations

from typing import Any

from relaydeck.client.features.ask_agent.model import eligibility
from relaydeck.domain.models import ConnectionState, unscope_key


def refresh(session: Any, state: Any, key: str) -> None:
    agent = state.inventory.agents.get(key)
    if agent is None or agent.host_id != session.host_id:
        return
    host = state.inventory.hosts.get(agent.host_id)
    session.store.update_command_context(host.epoch if host else "", agent.revision)
    if session.store.agent_status != agent.status.value:
        session.set_agent_status(agent.status.value)


def recipient(state: Any, key: str) -> tuple[str, str, str]:
    agent = state.inventory.agents.get(key)
    split = unscope_key(key)
    if agent is None or split != (agent.host_id, agent.terminal_id):
        return "", "unknown", "No available recipient."
    host = state.inventory.hosts.get(agent.host_id)
    if (
        state.revoked
        or state.granted_hosts is not None
        and agent.host_id not in state.granted_hosts
    ):
        return "", "unknown", "Access is unavailable."
    if host is None or not host.reachable or state.connection.state != ConnectionState.LIVE:
        return "", "unknown", "The Mac is offline."
    return host.epoch, agent.status.value, eligibility(agent.status.value)
