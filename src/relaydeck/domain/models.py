"""Immutable data types shared by inventory reducers and clients, with no UI, networking or I/O
dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.parse import quote, unquote

from relaydeck.contracts.catalog import AgentCatalog


class AgentStatus(str, Enum):
    IDLE = "idle"
    WORKING = "working"
    BLOCKED = "blocked"
    DONE = "done"
    UNKNOWN = "unknown"

    @classmethod
    def coerce(cls, value: str) -> AgentStatus:
        try:
            return cls(value)
        except ValueError:
            return cls.UNKNOWN


class ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    AUTHENTICATING = "authenticating"
    SYNCHRONIZING = "synchronizing"
    LIVE = "live"
    RECONNECTING = "reconnecting"
    HOST_OFFLINE = "host_offline"
    ACCESS_REVOKED = "access_revoked"


class EventType(str, Enum):
    HOST_STATUS = "host.status"
    WORKSPACE_UPSERTED = "workspace.upserted"
    TAB_UPSERTED = "tab.upserted"
    PANE_UPSERTED = "pane.upserted"
    AGENT_UPSERTED = "agent.upserted"
    RESOURCE_REMOVED = "resource.removed"
    COMMAND_UPDATED = "command.updated"
    CAPABILITIES_CHANGED = "capabilities.changed"
    RESYNC_REQUIRED = "resync.required"


class CommandStatus(str, Enum):
    ACCEPTED = "accepted"
    DISPATCHED = "dispatched"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


def scope_key(host_id: str, bare_id: str) -> str:
    """Encode a host-local ID as one URL-safe, host-scoped key for maps, selection and routes.
    Records and wire messages retain bare IDs.
    """
    return f"{quote(host_id, safe='')}:{quote(bare_id, safe='')}"


def unscope_key(scoped: str) -> tuple[str, str] | None:
    """Split a host-scoped key into (host_id, bare_id), returning None for empty or malformed
    keys.
    """
    host, sep, bare = scoped.partition(":")
    if not sep or not host or not bare or ":" in bare:
        return None
    return (unquote(host), unquote(bare))


def lookup[T](mapping: dict[str, T], scoped: str) -> T | None:
    """Scoped map lookup with a fail-closed host check.

    Returns the entry only when the key's host segment matches the
    record's own ``host_id``; anything else (missing key, malformed
    key, host mismatch) yields None instead of a wrong-host record.
    """
    value = mapping.get(scoped)
    if value is None:
        return None
    parts = unscope_key(scoped)
    if parts is None or getattr(value, "host_id", "") != parts[0]:
        return None
    return value


@dataclass(frozen=True)
class Host:
    id: str
    label: str
    epoch: str = ""
    last_seen_ms: int = 0
    reachable: bool = False
    protocol: int = 0
    version: str = ""
    capabilities: frozenset[str] = frozenset()
    agent_catalog: AgentCatalog | None = None


@dataclass(frozen=True)
class Workspace:
    id: str
    host_id: str
    number: int = 0
    label: str = ""
    focused: bool = False
    pane_count: int = 0
    tab_count: int = 0
    active_tab_id: str = ""
    agent_status: AgentStatus = AgentStatus.UNKNOWN


@dataclass(frozen=True)
class Tab:
    id: str
    host_id: str
    workspace_id: str
    number: int = 0
    label: str = ""
    focused: bool = False
    pane_count: int = 0
    agent_status: AgentStatus = AgentStatus.UNKNOWN


@dataclass(frozen=True)
class Pane:
    id: str
    host_id: str
    workspace_id: str
    tab_id: str
    focused: bool = False
    agent_status: AgentStatus = AgentStatus.UNKNOWN


@dataclass(frozen=True)
class Agent:
    terminal_id: str
    host_id: str
    workspace_id: str
    tab_id: str
    pane_id: str
    name: str = ""
    agent: str = ""
    title: str = ""
    status: AgentStatus = AgentStatus.UNKNOWN
    focused: bool = False
    launch_pending: bool = False
    interactive_ready: bool = False
    state_change_seq: int = 0
    revision: int = 0


@dataclass(frozen=True)
class Snapshot:
    host_id: str
    host_epoch: str
    cursor: str
    host_protocol: int = 0
    host_version: str = ""
    host_capabilities: frozenset[str] = frozenset()
    agent_catalog: AgentCatalog | None = None
    workspaces: tuple[Workspace, ...] = ()
    tabs: tuple[Tab, ...] = ()
    panes: tuple[Pane, ...] = ()
    agents: tuple[Agent, ...] = ()
    taken_ms: int = 0


@dataclass(frozen=True)
class MetadataEvent:
    event_id: str
    host_id: str
    host_epoch: str
    sequence: int
    cursor: str
    type: EventType
    resource_id: str = ""
    resource_revision: int = 0
    observed_ms: int = 0
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CommandRecord:
    command_id: str
    host_id: str
    kind: str
    target: dict[str, str]
    status: CommandStatus = CommandStatus.ACCEPTED
    payload_digest: str = ""
    error_code: str = ""
    created_ms: int = 0
    updated_ms: int = 0


@dataclass(frozen=True)
class Connection:
    state: ConnectionState = ConnectionState.DISCONNECTED
    host_id: str = ""
    cursor: str = ""
    last_sequence: int = 0
    detail: str = ""


@dataclass(frozen=True)
class Selection:
    host_id: str = ""
    # Host-scoped key (scope_key form) or "" when cleared. Bare upstream
    # ids are never valid here: select_session rejects them fail-closed.
    session_key: str = ""
    terminal_kind: str = ""
    generation: int = 0


@dataclass(frozen=True)
class Inventory:
    # Hosts use globally unique gateway IDs; resource maps use scope_key(host, bare).
    # Records retain bare source IDs, which may repeat across hosts.
    hosts: dict[str, Host] = field(default_factory=dict)
    workspaces: dict[str, Workspace] = field(default_factory=dict)
    tabs: dict[str, Tab] = field(default_factory=dict)
    panes: dict[str, Pane] = field(default_factory=dict)
    agents: dict[str, Agent] = field(default_factory=dict)
    truncated: bool = False


@dataclass(frozen=True)
class AppState:
    connection: Connection = field(default_factory=Connection)
    inventory: Inventory = field(default_factory=Inventory)
    selection: Selection = field(default_factory=Selection)
    commands: dict[str, CommandRecord] = field(default_factory=dict)
    stale: bool = False
    needs_resync: bool = False
    revoked: bool = False
    # None means grants have not been fetched; otherwise reject ungranted hosts.
    granted_hosts: frozenset[str] | None = None
