"""Bounded, ordered agent deltas; uncertainty requests a fresh inventory page."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

from relaydeck.domain.agent_order import metadata_order
from relaydeck.domain.metadata import AGENT_FIELDS, activity_id
from relaydeck.domain.models import AppState, EventType, MetadataEvent, scope_key

MAX_UPDATES = 64
_MEANINGFUL = ("status", "name", "title", "state_change_seq", "interactive_ready")


@dataclass(frozen=True, slots=True)
class AgentUpdate:
    event: MetadataEvent
    activity: bool


class InventoryUpdates:
    def __init__(self) -> None:
        self.version = 0
        self.history: deque[AgentUpdate] = deque(maxlen=MAX_UPDATES)

    def reset(self) -> None:
        self.version += 1
        self.history.clear()

    def observe(self, before: AppState, event: MetadataEvent, action: str) -> None:
        if action == "duplicate":
            return
        agent = before.inventory.agents.get(scope_key(event.host_id, event.resource_id))
        if (
            action != "applied"
            or event.type != EventType.AGENT_UPSERTED
            or agent is None
            or event.sequence != before.connection.last_sequence + 1
            or event.resource_revision < agent.revision
            or (
                event.resource_revision == agent.revision
                and event.payload.get("status_changed") is not True
            )
            or not event.payload
            or set(event.payload) - {*AGENT_FIELDS, "host_id", "status_changed"}
            or any(
                event.payload.get(key, getattr(agent, key)) != getattr(agent, key)
                for key in ("terminal_id", "pane_id", "tab_id", "workspace_id")
            )
        ):
            self.reset()
            return
        activity = (
            event.payload.get("status_changed") is True
            or any(
                event.payload.get(key, getattr(agent, key)) != getattr(agent, key)
                for key in _MEANINGFUL
                if key != "status"
            )
            or event.payload.get("status", agent.status.value) != agent.status.value
        )
        self.history.append(AgentUpdate(event, activity))

    def since(self, version: int, sequence: int, end: int) -> tuple[AgentUpdate, ...] | None:
        if version != self.version or end < sequence or end - sequence > MAX_UPDATES:
            return None
        updates = tuple(
            update for update in self.history if sequence < update.event.sequence <= end
        )
        if [u.event.sequence for u in updates] != list(range(sequence + 1, end + 1)):
            return None
        return updates


def apply_rows(rows: dict[str, dict[str, Any]], updates: tuple[AgentUpdate, ...]) -> bool:
    """Preserve all untouched row objects and the captured unseen-page frontiers."""
    staged = dict(rows)
    for update in updates:
        event = update.event
        key = scope_key(event.host_id, event.resource_id)
        row = staged.get(key)
        if (
            row is None
            or row.get("host_epoch") != event.host_epoch
            or row.get("revision", 0) > event.resource_revision
        ):
            return False
        changed = {
            **row,
            **{key: event.payload[key] for key in AGENT_FIELDS if key in event.payload},
            "revision": event.resource_revision,
        }
        if update.activity:
            changed.update(
                activity_id=activity_id(event.host_epoch, event.event_id, event.resource_id),
                last_activity_ms=event.observed_ms,
                unread=True,
            )
        staged[key] = changed
    if updates:
        ordered = sorted(staged.items(), key=lambda pair: (*metadata_order(pair[1])[:2], pair[0]))
        rows.clear()
        rows.update(ordered)
    return True
