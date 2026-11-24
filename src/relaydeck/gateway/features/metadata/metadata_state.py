"""Fold metadata transitions into coherent inventory snapshots without retaining command history
there. Fresh clients recover outstanding commands through status reads.
"""

from typing import Any

from relaydeck.domain.metadata import AGENT_FIELDS
from relaydeck.domain.models import AgentStatus


def materialize_agent(
    snapshots: dict[str, dict[str, Any]], host: str, event: dict[str, Any]
) -> None:
    snapshot = snapshots.get(host)
    payload = event.get("payload", {})
    if snapshot is None or snapshot.get("host", {}).get("epoch") != event.get("host_epoch"):
        return
    if event.get("type") == "pane.upserted":
        status = payload.get("agent_status")
        if not isinstance(status, str) or status not in {s.value for s in AgentStatus}:
            return
        pane_id = event.get("resource_id")
        # Legacy status seeds must survive a later delta's cursor advancement too.
        snapshots[host] = {
            **snapshot,
            "agents": [
                {**row, "status": status} if row.get("pane_id") == pane_id else row
                for row in snapshot.get("agents", [])
            ],
            "panes": [
                {**pane, "agent_status": status} if pane.get("id") == pane_id else pane
                for pane in snapshot.get("panes", [])
            ],
        }
        return
    if event.get("type") != "agent.upserted" or payload.get("status_changed") is not True:
        return
    agents = snapshot.get("agents", [])
    for index, row in enumerate(agents):
        if row.get("terminal_id") != event.get("resource_id"):
            continue
        revision = event.get("resource_revision", 0)
        if revision < row.get("revision", 0):
            return
        rows = list(agents)
        rows[index] = {
            **row,
            **{key: payload[key] for key in AGENT_FIELDS if key in payload},
            "revision": revision,
        }
        panes = [
            {**pane, "agent_status": rows[index]["status"]}
            if pane.get("id") == rows[index].get("pane_id")
            else pane
            for pane in snapshot.get("panes", [])
        ]
        snapshots[host] = {**snapshot, "agents": rows, "panes": panes, "cursor": event["cursor"]}
        return
