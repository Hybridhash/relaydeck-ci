"""Creation command builders. Pure mapping from
validated drafts to gateway command envelopes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from relaydeck.client.models.commands.session_close import envelope
from relaydeck.contracts.launch import default_agent_name


def agent_start_command(
    *,
    command_id: str,
    host_id: str,
    host_epoch: str,
    workspace_id: str,
    kind: str,
    name: str = "",
    argv: list[str] | None = None,
    now: datetime | None = None,
    existing_names: frozenset[str] = frozenset(),
    new_workspace: dict[str, str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": kind,
        "name": name.strip() or default_agent_name(kind, existing_names),
    }
    if argv:
        payload["argv"] = list(argv)
    if new_workspace is not None:
        payload["new_workspace"] = dict(new_workspace)
    return envelope(
        command_id=command_id,
        host_id=host_id,
        host_epoch=host_epoch,
        kind="agent.start",
        target={"workspace_mode": "new"}
        if new_workspace is not None
        else {"workspace_id": workspace_id},
        payload=payload,
        now=now,
    )


def shell_create_command(
    *,
    command_id: str,
    host_id: str,
    host_epoch: str,
    workspace_id: str,
    label: str = "",
    now: datetime | None = None,
    new_workspace: dict[str, str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if label:
        payload["label"] = label
    if new_workspace is not None:
        payload["new_workspace"] = dict(new_workspace)
    return envelope(
        command_id=command_id,
        host_id=host_id,
        host_epoch=host_epoch,
        kind="shell.create",
        target={"workspace_mode": "new"}
        if new_workspace is not None
        else {"workspace_id": workspace_id},
        payload=payload,
        now=now,
    )
