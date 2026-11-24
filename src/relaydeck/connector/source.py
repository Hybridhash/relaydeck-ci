"""Translate verified Herdr inventory and events and relay exact attachment bytes through
gateway-supplied epochs. Own subscriptions, attachment recovery and bounded usage-linking
evidence.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import random
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from relaydeck.connector.attach import (
    AttachmentPool,
    AttachSession,
    ControlSession,
    ObserveSession,
    PoolFullError,
    ToolUnavailable,
    plan_agent_attach,
    plan_shell_attach,
)
from relaydeck.connector.attach_lifecycle import AttachLifecycle
from relaydeck.connector.herdr import (
    HerdrAdapter,
    HerdrError,
    HerdrProtocolError,
    Subscription,
    error_label,
)
from relaydeck.connector.journal import DispatchAdapter
from relaydeck.connector.metadata import SubscriptionReads
from relaydeck.contracts.protocol import EXPECTED_PROTOCOL
from relaydeck.contracts.schemas import SchemaError, validate
from relaydeck.usage import api as proc_link
from relaydeck.usage.api import (
    READERS,
    MappingGap,
    PaneCwdObservation,
    SessionMapping,
    UsageSource,
    session_text,
)

__all__ = [
    "AGENT_STATUSES",
    "BOOT_EPOCH_PREFIX",
    "DEFAULT_SUBSCRIPTIONS",
    "TUNNEL_MAX_OUTPUT",
    "BootstrapData",
    "HerdrSource",
    "LinkSource",
    "LiveSource",
    "OutputFrame",
    "TerminalRelay",
    "translate_event",
    "translate_snapshot",
]

LOG = logging.getLogger("relaydeck.connector.source")
MAX_USAGE_EPISODES = 1024  # At most 16 configured sources: below the store's mapping batch bound.
MAX_USAGE_PANE_CWD = 1024  # Pane cwd episodes; exhaustion drops inference, never exact links.
MAX_PROC_EPISODES = 1024  # Rank-2 process episodes; separate pool, same bound.


@dataclass
class _UsageEpisode:
    pane: str
    workspace: str
    agent: str
    ref_kind: str
    ref_source: str
    value: str
    interval: str
    first: int
    last: int
    tab: str = ""
    persisted: dict[str, int] = field(default_factory=dict)
    generation: int = 0
    label: str = ""
    repo_fp: str | None = None


@dataclass
class _ProcEpisode:
    """One rank-2 episode: a pane's pid held a session lock open in a space. `interval` carries
    a "p2:" prefix so rank-2 intervals never collide with rank-1 ids.
    """

    pane: str
    source_key: str
    workspace: str
    kind: str
    session: str
    interval: str
    first: int
    last: int
    pid: int
    start: str
    persisted: dict[str, int] = field(default_factory=dict)
    generation: int = 0
    label: str = ""
    repo_fp: str | None = None


@dataclass
class _ProcPane:
    """Lightweight pane record retained for process-probing eligibility."""

    kind: str | None
    space: str | None
    has_session: bool


@dataclass
class _UsageSpace:
    """One workspace id's generation. Herdr reuses ids across server restarts, so the generation
    bumps when the id disappears from a complete snapshot, and on reconnect when the stored
    (label, repo) fingerprint no longer matches.
    """

    generation: int = 0
    label: str = ""
    repo: str | None = None
    present: bool = True


@dataclass
class _UsagePaneDigest:
    """One pane's episode with one working-folder digest in one (space, generation)."""

    pane: str
    digest: str
    kind: str
    workspace: str
    generation: int
    interval: str
    first: int
    last: int
    persisted: dict[str, int] = field(default_factory=dict)


@dataclass
class _UsageGap:
    gap_id: str
    first: int
    last: int | None = None
    persisted: dict[str, int | None] = field(default_factory=dict)


# Backoff before retrying a failed per-pane re-subscription, held or not, so a sick daemon is not
# hit on every tick. Jitter is seeded (pane_sub_seed) to stay deterministic in tests.
_PANE_SUB_RETRY_MS = 30_000
_PANE_SUB_RETRY_JITTER_MS = 5_000

# Gateway agent_status enum, identical to the upstream AgentStatus enum
# (both verified: contracts schemas + herdr-0.9.1-schema.json).
AGENT_STATUSES = frozenset({"idle", "working", "blocked", "done", "unknown"})

BOOT_EPOCH_PREFIX = "herdr"

# Mirror gateway/http/bounds.py TUNNEL_MAX_OUTPUT when chunking relay frames.
TUNNEL_MAX_OUTPUT = 64 * 1024
MAX_RELAY_BYTES = 1024 * 1024
# Resize requests settle this long before one is applied (one repaint for a burst).
RESIZE_SETTLE_S = 0.15
# A controlled pane whose frames report another size is re-asserted at most this often.
GEOMETRY_REASSERT_S = 2.0

# Global subscriptions need no pane ids, so one ack covers the bootstrap with no missed-event
# window; per-pane status subscriptions open post-snapshot (see _ensure_pane_subscriptions).
# output_matched stays out: previews must not ride the exact-byte stream.
DEFAULT_SUBSCRIPTIONS: tuple[dict[str, str], ...] = (
    {"type": "workspace.updated"},
    {"type": "tab.created"},
    {"type": "tab.closed"},
    {"type": "pane.created"},
    {"type": "pane.updated"},
    {"type": "pane.closed"},
    {"type": "pane.exited"},
    {"type": "layout.updated"},
)

# Map evidenced event shapes and IDs only; exclude output previews from the exact-byte stream.
_EVENT_MAP: dict[str, tuple[str, str, bool]] = {
    "pane.agent_status_changed": ("pane.upserted", "pane_id", False),
    "workspace.created": ("workspace.upserted", "workspace_id", False),
    "workspace.updated": ("workspace.upserted", "workspace_id", False),
    "workspace.metadata_updated": ("workspace.upserted", "workspace_id", False),
    "workspace.renamed": ("workspace.upserted", "workspace_id", False),
    "workspace.moved": ("workspace.upserted", "workspace_id", False),
    "workspace.reordered": ("workspace.upserted", "workspace_id", False),
    "workspace.focused": ("workspace.upserted", "workspace_id", False),
    "workspace.closed": ("resource.removed", "workspace_id", True),
    "tab.created": ("tab.upserted", "tab_id", False),
    "tab.focused": ("tab.upserted", "tab_id", False),
    "tab.renamed": ("tab.upserted", "tab_id", False),
    "tab.moved": ("tab.upserted", "tab_id", False),
    "tab.closed": ("resource.removed", "tab_id", True),
    "pane.created": ("pane.upserted", "pane_id", False),
    "pane.updated": ("pane.upserted", "pane_id", False),
    "pane.focused": ("pane.upserted", "pane_id", False),
    "pane.moved": ("pane.upserted", "pane_id", False),
    "pane.closed": ("resource.removed", "pane_id", True),
    "pane.exited": ("resource.removed", "pane_id", True),
}


# an upstream removal means the inventory snapshot may hold a closed tab or pane
# even when the live event itself does not translate, so the link re-reads and re-pushes it.
REMOVAL_EVENTS = frozenset({"tab.closed", "pane.closed", "pane.exited", "workspace.closed"})


class LinkSource(Protocol):
    """Runtime port the connector link runs against (fixture and
    production sources both satisfy this; the link knows no concrete
    source type)."""

    @property
    def dispatch_adapter(self) -> DispatchAdapter: ...
    def snapshot_document(self, host_id: str) -> dict[str, Any]: ...
    def drain_outputs(self) -> list[tuple[str, str, bytes]]: ...


@runtime_checkable
class LiveSource(LinkSource, Protocol):
    """Live-relay extension of the link port. The link probes for this port at runtime: sources
    without it (fixtures) keep the connect-time push + dispatch behavior, while live sources
    additionally stream post-bootstrap events and PTY bytes.
    """

    async def collect_live(self, timeout: float) -> list[dict[str, Any]]:
        """Drain translated live event partials (empty when quiet).

        Raises HerdrProtocolError("upstream_closed") when the daemon
        closed the global subscription: the caller must drop the
        session and `rebootstrap` (new continuity epoch)."""
        ...

    def pump_live(self) -> int:
        """Pump attached PTY bytes for epoch-mapped terminals into the relay (drained via
        `drain_outputs`). Dead sessions are reaped with epochs kept: observers use timed
        retry, control shells re-note, and control agents require explicit takeover.
        """
        ...

    def note_epochs(self, routes: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
        """Apply the gateway epoch table ({terminal_id: {"epoch",
        "kind", "mode"}} with kind shell|agent and mode control|observe)
        and best-effort auto-attach
        newly mapped terminals (replacing dead sessions, whose last
        bytes are relayed under the gateway-provided epoch). Never
        raises: returns per-terminal failures ({terminal_id: reason})
        for the link to log loudly."""
        ...

    def drain_seeds(self) -> list[dict[str, Any]]:
        """Consume bootstrap seed partials exactly once (rebootstrap
        seeds a fresh batch for the next session push)."""
        ...

    async def resync(self) -> None:
        """Refresh snapshot content without a new continuity boundary
        (same epoch): reconnect freshness, not a reconnection."""
        ...

    async def rebootstrap(self) -> str:
        """Full re-bootstrap after upstream death (new epoch, fresh
        subscriptions, attach sweep; gateway epochs persist so the next
        `note_epochs` respawns). Returns the new epoch."""
        ...


def _clip(value: Any, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _count(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _flag(value: Any) -> bool:
    return value if isinstance(value, bool) else False


def _status(value: Any) -> str:
    return value if isinstance(value, str) and value in AGENT_STATUSES else "unknown"


def wire_event_name(name: str) -> str:
    """Normalize the first underscore in Herdr event names to the dotted subscription form.
    Leave already-dotted names unchanged.
    """
    return name if "." in name else name.replace("_", ".", 1)


# nested created/updated objects carry the fields the reducer upserts from.
_TAB_PAYLOAD_KEYS = ("workspace_id", "number", "label", "focused", "pane_count")
_PANE_PAYLOAD_KEYS = ("workspace_id", "tab_id", "focused")
# Throttle for the skipped-event counter log: at most once per this many seconds.
SKIP_LOG_INTERVAL_S = 60.0


def translate_snapshot(
    host_id: str, epoch: str, session: dict[str, Any], agents: list[dict[str, Any]]
) -> dict[str, Any]:
    """Translate snapshot and agent-list data, clipping display fields to gateway bounds. Reject
    invalid top-level/result shapes and skip entries that lack identity.
    """
    if not isinstance(session, dict):
        raise ValueError("session snapshot is not an object")
    if session.get("protocol") != EXPECTED_PROTOCOL:
        raise ValueError("snapshot protocol drift")
    for key in ("workspaces", "tabs", "panes"):
        if not isinstance(session.get(key), list):
            raise ValueError(f"snapshot.{key} is not a list")
    workspaces: list[dict[str, Any]] = []
    for entry in session["workspaces"]:
        if not isinstance(entry, dict) or not entry.get("workspace_id"):
            continue
        workspaces.append(
            {
                "id": _clip(entry.get("workspace_id"), 128),
                "host_id": host_id,
                "number": _count(entry.get("number")),
                "label": _clip(entry.get("label"), 128),
                "focused": _flag(entry.get("focused")),
                "pane_count": _count(entry.get("pane_count")),
                "tab_count": _count(entry.get("tab_count")),
                "active_tab_id": _clip(entry.get("active_tab_id"), 128),
                "agent_status": _status(entry.get("agent_status")),
            }
        )
    tabs: list[dict[str, Any]] = []
    for entry in session["tabs"]:
        if not isinstance(entry, dict) or not entry.get("tab_id"):
            continue
        tabs.append(
            {
                "id": _clip(entry.get("tab_id"), 128),
                "host_id": host_id,
                "workspace_id": _clip(entry.get("workspace_id"), 128),
                "number": _count(entry.get("number")),
                "label": _clip(entry.get("label"), 128),
                "focused": _flag(entry.get("focused")),
                "pane_count": _count(entry.get("pane_count")),
                "agent_status": _status(entry.get("agent_status")),
            }
        )
    panes: list[dict[str, Any]] = []
    for entry in session["panes"]:
        if not isinstance(entry, dict) or not entry.get("pane_id"):
            continue
        panes.append(
            {
                "id": _clip(entry.get("pane_id"), 128),
                "host_id": host_id,
                "workspace_id": _clip(entry.get("workspace_id"), 128),
                "tab_id": _clip(entry.get("tab_id"), 128),
                "focused": _flag(entry.get("focused")),
                "agent_status": _status(entry.get("agent_status")),
            }
        )
    # agent.list wins over the snapshot copy (fresher state_change_seq).
    merged: dict[str, dict[str, Any]] = {}
    for entry in list(session.get("agents") or []) + list(agents):
        if not isinstance(entry, dict) or not entry.get("terminal_id"):
            continue
        merged[str(entry["terminal_id"])] = entry
    out_agents: list[dict[str, Any]] = []
    for _terminal_id, entry in sorted(merged.items()):
        if not entry.get("workspace_id") or not entry.get("tab_id") or not entry.get("pane_id"):
            continue
        out_agents.append(_agent_row(host_id, entry))
    doc: dict[str, Any] = {
        "host": {
            "id": host_id,
            "label": "Herdr",
            "epoch": epoch,
            "reachable": True,
            "protocol": EXPECTED_PROTOCOL,
            "version": _clip(session.get("version", ""), 32),
        },
        # Placeholder: the gateway stamps the HMAC resume cursor at ingest.
        "cursor": "herdr-bootstrap",
        "workspaces": workspaces,
        "tabs": tabs,
        "panes": panes,
        "agents": out_agents,
    }
    try:
        validate("relaydeck/gateway/v1/snapshot", doc)
    except SchemaError as exc:
        raise ValueError(f"translated snapshot invalid: {exc}") from exc
    return doc


def _agent_row(host_id: str, entry: dict[str, Any]) -> dict[str, Any]:
    title = entry.get("terminal_title") or entry.get("title") or entry.get("name") or ""
    return {
        "terminal_id": _clip(entry.get("terminal_id"), 128),
        "host_id": host_id,
        "workspace_id": _clip(entry.get("workspace_id"), 128),
        "tab_id": _clip(entry.get("tab_id"), 128),
        "pane_id": _clip(entry.get("pane_id"), 128),
        "name": _clip(entry.get("name"), 128),
        "agent": _clip(entry.get("agent"), 64),
        "title": _clip(title, 256),
        "status": _status(entry.get("agent_status")),
        "focused": _flag(entry.get("focused")),
        "interactive_ready": _flag(entry.get("interactive_ready")),
        "state_change_seq": _count(entry.get("state_change_seq")),
        "revision": _count(entry.get("revision")),
    }


def translate_event(
    name: str, data: dict[str, Any], *, epoch: str, event_id: str, observed_at: str
) -> dict[str, Any] | None:
    """Translate one Herdr subscription event into a gateway event
    partial (sequence/cursor/protocol/host are server-stamped). Returns
    None for unmapped names or evidence-missing identity: skipped and
    counted by the caller, never invented."""
    mapped = _EVENT_MAP.get(name)
    if mapped is None or not isinstance(data, dict):
        return None
    gateway_type, id_key, _ = mapped
    resource_id, nested = _resource_ref(data, id_key)
    if resource_id is None:
        return None
    partial: dict[str, Any] = {
        "event_id": event_id,
        "host_epoch": epoch,
        "type": gateway_type,
        "resource_id": resource_id[:128],
        "observed_at": observed_at,
    }
    if name == "pane.agent_status_changed" and isinstance(data.get("agent_status"), str):
        partial["payload"] = {"agent_status": data["agent_status"]}
    elif gateway_type in ("tab.upserted", "pane.upserted") and nested:
        partial["payload"] = _upsert_payload(gateway_type, nested)
    elif gateway_type == "workspace.upserted":
        label = nested.get("label", data.get("label"))
        if isinstance(label, str):
            partial["payload"] = {"label": _clip(label, 128)}
    return partial


def _resource_ref(data: dict[str, Any], id_key: str) -> tuple[str | None, dict[str, Any]]:
    """the resource id is top-level (`tab_closed`: `data.tab_id`) or nested in
    the object named by the id key (`tab_created`: `data.tab.tab_id`). Returns (id, nested)."""
    direct = data.get(id_key)
    if isinstance(direct, str) and direct:
        return direct, {}
    nested = data.get(id_key[: -len("_id")])
    if isinstance(nested, dict):
        inner = nested.get(id_key)
        if isinstance(inner, str) and inner:
            return inner, nested
    return None, {}


def _upsert_payload(gateway_type: str, nested: dict[str, Any]) -> dict[str, Any]:
    keys = _TAB_PAYLOAD_KEYS if gateway_type == "tab.upserted" else _PANE_PAYLOAD_KEYS
    payload = {key: nested[key] for key in keys if nested.get(key) is not None}
    if "agent_status" in nested:
        payload["agent_status"] = _status(nested["agent_status"])
    return payload


def _pane_id_from_event(data: dict[str, Any]) -> str | None:
    """Extract the pane identity from a live event payload.

    Subscription event data carries either a top-level `pane_id`
    (pane.agent_status_changed golden) or a nested `pane` object
    (pane.created/updated per the event schema); anything else yields
    None (no invention)."""
    direct = data.get("pane_id")
    if isinstance(direct, str) and direct:
        return direct
    nested = data.get("pane")
    if isinstance(nested, dict):
        pane_id = nested.get("pane_id")
        if isinstance(pane_id, str) and pane_id:
            return pane_id
    return None


@dataclass(frozen=True)
class OutputFrame:
    """One typed terminal-output frame: exact PTY bytes for a terminal
    at a gateway ring epoch. `data` never exceeds TUNNEL_MAX_OUTPUT."""

    terminal_id: str
    epoch: str
    data: bytes


class TerminalRelay:
    """Relay exact attachment bytes in bounded, per-terminal frames without previews or
    re-encoding. On overflow, fence that terminal for resync while other terminals keep
    flowing.
    """

    def __init__(self) -> None:
        self._frames: list[OutputFrame] = []
        self._reserved_bytes = 0
        self._staged_bytes = 0
        self.overflowed: dict[str, str] = {}
        self.overflow_count = 0

    def __len__(self) -> int:
        return len(self._frames)

    @property
    def load(self) -> int:
        """Bytes held: staged plus handed to a sender and not yet released."""
        return self._staged_bytes + self._reserved_bytes

    def script(self, terminal_id: str, epoch: str, data: bytes) -> int:
        """Stage exact bytes and return the number of frames staged, or zero on overflow or an
        already fenced terminal. Missing identity or empty bytes raises ValueError.
        """
        if not terminal_id or not epoch or not isinstance(data, bytes) or not data:
            raise ValueError("relay needs terminal_id, epoch, and bytes")
        if terminal_id in self.overflowed:
            return 0
        if self.load + len(data) > MAX_RELAY_BYTES:
            self.drop_terminal(terminal_id)
            self.overflowed[terminal_id] = epoch
            self.overflow_count += 1
            return 0
        staged = 0
        for at in range(0, len(data), TUNNEL_MAX_OUTPUT):
            self._frames.append(OutputFrame(terminal_id, epoch, data[at : at + TUNNEL_MAX_OUTPUT]))
            staged += 1
        self._staged_bytes += len(data)
        return staged

    def drop_terminal(self, terminal_id: str) -> int:
        """Discard one terminal's staged frames; returns the bytes dropped. Frames already
        handed to a sender are fenced by their epoch."""
        kept = [frame for frame in self._frames if frame.terminal_id != terminal_id]
        dropped = self._staged_bytes - sum(len(frame.data) for frame in kept)
        self._frames = kept
        self._staged_bytes -= dropped
        return dropped

    def clear_overflow(self, terminal_ids: Iterable[str]) -> None:
        for terminal_id in terminal_ids:
            self.overflowed.pop(terminal_id, None)

    def drain(self, *, reserve: bool = False) -> list[OutputFrame]:
        """Consume staged frames (each drain delivers bytes exactly once)."""
        frames, self._frames = self._frames, []
        self._staged_bytes = 0
        if reserve:
            self._reserved_bytes += sum(len(frame.data) for frame in frames)
        return frames

    def release(self, size: int) -> None:
        """Release a sender batch; queued + in-flight bytes share the same cap."""
        if not 0 <= size <= self._reserved_bytes:
            raise ValueError("invalid relay reservation release")
        self._reserved_bytes -= size

    @staticmethod
    def encode_tunnel(host_id: str, frame: OutputFrame) -> dict[str, Any]:
        return {
            "kind": "terminal.output",
            "host_id": host_id,
            "terminal_id": frame.terminal_id,
            "epoch": frame.epoch,
            "data_b64": base64.b64encode(frame.data).decode(),
        }


@dataclass(frozen=True)
class BootstrapData:
    epoch: str
    seed_events: tuple[dict[str, Any], ...]
    buffered_total: int
    skipped_events: int


# Read-only taps have a separate ceiling from the five control-retention
# slots. Excess routes are deferred, never admitted by evicting a healthy tap.
MAX_OBSERVE_SLOTS = 16
_OBSERVE_RETRY_DELAYS_MS = (1000, 2000, 4000, 8000, 16000, 30000)
_OBSERVE_STABLE_MS = 30000


@dataclass
class _ObserveRetry:
    attempt: int
    deadline_ms: int | None
    reason: str


class HerdrSource:
    """Production link source over a Herdr adapter (dispatch +
    bootstrap + metadata + relay). One instance serves one link run;
    epochs are per bootstrap (new continuity boundary after every
    upstream reconnection, per the contract)."""

    def __init__(
        self,
        adapter: HerdrAdapter,
        *,
        subscriptions: list[dict[str, Any]] | None = None,
        clock: Callable[[], int] | None = None,
        boot_tag: str | None = None,
        binary: str | None = "herdr",
        pool: AttachmentPool | None = None,
        observer_pool: AttachmentPool | None = None,
        retry_clock: Callable[[], float] = time.monotonic,
        pane_sub_seed: int | None = None,
        agent_kinds: tuple[str, ...] = (),
        launch_programs: Mapping[str, str | None] | None = None,
        proc_probe: proc_link.ProbeFn | None = None,
        proc_clock: Callable[[], float] = time.monotonic,
        controller_viewport: bool = False,
    ) -> None:
        self._adapter = adapter
        # companion.toml [terminal] controller_viewport: control attaches through Herdr's
        # controller viewport (the pane follows the viewer's grid) instead of the raw client.
        self._controller_viewport = controller_viewport
        # the owner's allowlist (companion.toml agent_kinds); a kind also
        # has to be on PATH to be reported or started. Empty = nothing launchable.
        self._agent_kinds = tuple(agent_kinds)
        # kind -> the program Herdr runs for it, built once by the companion; a configured kind
        # it does not name is looked up by its own name.
        self._launch_programs = MappingProxyType(dict(launch_programs or {}))
        self._subscriptions = (
            [dict(entry) for entry in subscriptions]
            if subscriptions is not None
            else [dict(entry) for entry in DEFAULT_SUBSCRIPTIONS]
        )
        self._clock = clock
        self._boot_tag = boot_tag if boot_tag is not None else secrets.token_hex(4)
        self._binary = binary
        self._attach_io: AttachLifecycle | None = None
        self._pool = pool if pool is not None else AttachmentPool()
        # Observers are read-only taps, not control-retention slots. Never
        # evict a healthy tap just because another mapped observer arrives.
        self._observer_pool = (
            observer_pool if observer_pool is not None else AttachmentPool(slots=MAX_OBSERVE_SLOTS)
        )
        self._observe_retries: dict[str, _ObserveRetry] = {}
        self._observe_started: dict[str, int] = {}
        self._observe_issues: dict[str, tuple[str, int | None, str]] = {}
        self._retry_clock = retry_clock
        self._rng = random.Random(pane_sub_seed)  # noqa: S311 - jitter, not security
        self._attached: dict[str, tuple[AttachSession, str, str]] = {}
        self._output_changed = asyncio.Event()
        self._cancel_output_wait: Callable[[], None] | None = None
        self._relay = TerminalRelay()
        self._subscription: Subscription | None = None
        self._metadata_reads = SubscriptionReads()
        self._pane_subscription: Subscription | None = None
        self._pane_want: set[str] = set()
        self._pane_have: frozenset[str] = frozenset()
        self._pane_dirty = False
        self._snapshot_dirty = False
        self._skip_logged_at = float("-inf")
        self._pane_retry_after_ms = 0
        self._epochs: dict[str, str] = {}
        self._epoch_kinds: dict[str, str] = {}
        self._epoch_modes: dict[str, str] = {}
        # An ended agent attach requires a new explicit-takeover epoch before it can respawn.
        self._agent_lost: dict[str, str] = {}
        self._terminal_resets: dict[str, str] = {}
        # Resize requests are coalesced per terminal: the latest size wins and is applied once the
        # requests go quiet, so a resume/rotation burst causes one repaint, not one per event.
        self._resize_pending: dict[str, tuple[str, int, int]] = {}
        self._resize_timers: dict[str, asyncio.TimerHandle] = {}
        # Last size applied to each controlled terminal, when it was last re-asserted, and the
        # pending correction timer for a mismatch seen inside the cooldown.
        self._geometry: dict[str, tuple[int, int]] = {}
        self._reasserted_at: dict[str, float] = {}
        self._correction_timers: dict[str, asyncio.TimerHandle] = {}
        # Control attaches Herdr refused because another client owns the terminal, by epoch:
        # never respawned under that epoch; the gateway gets a typed refusal instead of a reset.
        self._attach_refused: dict[str, str] = {}
        self._snapshot: dict[str, Any] | None = None
        self._agents: list[dict[str, Any]] = []
        self._seeds: list[dict[str, Any]] = []
        self._boots = 0
        self._skipped = 0
        self._event_seq = 0
        self.relay = self._relay
        self._usage_lock = threading.Lock()
        self._usage_sources: tuple[UsageSource, ...] = ()
        self._usage_episodes: OrderedDict[str, _UsageEpisode] = OrderedDict()
        self._usage_active: dict[str, str] = {}
        self._usage_gaps: dict[str, _UsageGap] = {}
        self._usage_spaces: dict[str, _UsageSpace] = {}
        self._usage_cwd_episodes: OrderedDict[str, _UsagePaneDigest] = OrderedDict()
        self._usage_cwd_active: dict[str, str] = {}
        self._usage_digest: Callable[[str], str | None] | None = None
        self._usage_repo_fp: Callable[[str], str | None] | None = None
        self._proc_episodes: OrderedDict[str, _ProcEpisode] = OrderedDict()
        self._proc_active: dict[tuple[str, str, str], str] = {}
        self._proc_panes: dict[str, _ProcPane] = {}
        self._proc_cursor = 0
        self._proc_last_tick: int | None = None
        self._proc_clock = proc_clock
        self._proc_probe = proc_probe or proc_link.default_probe((binary,) if binary else ())

    def configure_usage(
        self,
        sources: tuple[UsageSource, ...],
        *,
        digest: Callable[[str], str | None] | None = None,
        repo_fingerprint: Callable[[str], str | None] | None = None,
    ) -> None:
        """Configure before bootstrap; the numeric collector calls usage_mappings off-thread."""
        with self._usage_lock:
            self._usage_sources = sources
            self._usage_digest = digest
            self._usage_repo_fp = repo_fingerprint

    def usage_mappings(self) -> tuple[SessionMapping, ...]:
        """Rank-1 episodes plus rank-2 process episodes; the tick runs first."""
        self._proc_tick_maybe()
        with self._usage_lock:
            by_key = {s.source_key: s for s in self._usage_sources}
            return tuple(
                SessionMapping(
                    source.source_key,
                    episode.agent,
                    episode.value,
                    episode.interval,
                    source.host_id,
                    episode.workspace,
                    episode.first,
                    episode.last,
                    generation=episode.generation,
                    label=episode.label or None,
                    repo_fp=episode.repo_fp,
                )
                for episode in self._usage_episodes.values()
                for source in self._usage_sources
                if source.kind == episode.agent
            ) + tuple(
                SessionMapping(
                    episode.source_key,
                    episode.kind,
                    episode.session,
                    episode.interval,
                    by_key[episode.source_key].host_id,
                    episode.workspace,
                    episode.first,
                    episode.last,
                    generation=episode.generation,
                    label=episode.label or None,
                    repo_fp=episode.repo_fp,
                    rank=2,
                )
                for episode in self._proc_episodes.values()
                if episode.source_key in by_key
            )

    def usage_cwd_observations(self) -> tuple[PaneCwdObservation, ...]:
        """Pane-side cwd evidence for inferred linking; digests only, never raw folders."""
        with self._usage_lock:
            return tuple(
                PaneCwdObservation(
                    source.source_key,
                    episode.kind,
                    episode.interval,
                    source.host_id,
                    episode.digest,
                    episode.workspace,
                    episode.generation,
                    episode.first,
                    episode.last,
                )
                for episode in self._usage_cwd_episodes.values()
                for source in self._usage_sources
                if source.kind == episode.kind
            )

    def usage_mapping_gaps(self) -> tuple[MappingGap, ...]:
        with self._usage_lock:
            return tuple(
                MappingGap(source.source_key, gap.gap_id, gap.first, gap.last)
                for source in self._usage_sources
                if (gap := self._usage_gaps.get(source.kind)) is not None
            )

    def acknowledge_usage(
        self, source_key: str, mappings: tuple[SessionMapping, ...], gaps: tuple[MappingGap, ...]
    ) -> None:
        """Collector calls ONLY after apply returns (not when taking a snapshot).

        An observation arriving during apply may extend/close an episode; acknowledging
        the older last-seen cannot make that final update evictable.
        """
        with self._usage_lock:
            for mapping in mappings:
                episode = self._usage_episodes.get(mapping.interval_id)
                if episode is not None:
                    match = (
                        mapping.source_key == source_key
                        and mapping.session_key == episode.value
                        and mapping.first_seen_ms == episode.first
                    )
                    persisted = episode.persisted
                else:
                    proc = self._proc_episodes.get(mapping.interval_id)
                    if proc is None:
                        continue
                    match = (
                        mapping.source_key == proc.source_key == source_key
                        and mapping.session_key == proc.session
                        and mapping.first_seen_ms == proc.first
                    )
                    persisted = proc.persisted
                if match:
                    persisted[source_key] = max(mapping.last_seen_ms, persisted.get(source_key, -1))
            for source in self._usage_sources:
                if source.source_key != source_key:
                    continue
                gap = self._usage_gaps.get(source.kind)
                if gap is None:
                    continue
                for row in gaps:
                    if (
                        row.source_key == source_key
                        and row.gap_id == gap.gap_id
                        and (row.first_ms, row.last_ms) == (gap.first, gap.last)
                    ):
                        gap.persisted[source_key] = row.last_ms
                required = {s.source_key for s in self._usage_sources if s.kind == source.kind}
                if gap.last is not None and all(
                    key in gap.persisted and gap.persisted[key] == gap.last for key in required
                ):
                    del self._usage_gaps[source.kind]

    def acknowledge_cwd(
        self, source_key: str, observations: tuple[PaneCwdObservation, ...]
    ) -> None:
        """Collector calls ONLY after apply returns; mirrors acknowledge_usage."""
        with self._usage_lock:
            for row in observations:
                episode = self._usage_cwd_episodes.get(row.interval_id)
                if (
                    episode is not None
                    and row.source_key == source_key
                    and row.digest == episode.digest
                    and row.first_seen_ms == episode.first
                ):
                    episode.persisted[source_key] = max(
                        row.last_seen_ms, episode.persisted.get(source_key, -1)
                    )

    def _usage_make_room(self) -> bool:
        if len(self._usage_episodes) < MAX_USAGE_EPISODES:
            return True
        active = set(self._usage_active.values())
        for key, episode in self._usage_episodes.items():
            required = {s.source_key for s in self._usage_sources if s.kind == episode.agent}
            if key not in active and all(
                episode.persisted.get(source, -1) >= episode.last for source in required
            ):
                del self._usage_episodes[key]
                return True
        return False

    def _usage_cwd_make_room(self) -> bool:
        if len(self._usage_cwd_episodes) < MAX_USAGE_PANE_CWD:
            return True
        active = set(self._usage_cwd_active.values())
        for key, episode in self._usage_cwd_episodes.items():
            required = {s.source_key for s in self._usage_sources if s.kind == episode.kind}
            if key not in active and all(
                episode.persisted.get(source, -1) >= episode.last for source in required
            ):
                del self._usage_cwd_episodes[key]
                return True
        return False

    def _proc_make_room(self) -> bool:
        if len(self._proc_episodes) < MAX_PROC_EPISODES:
            return True
        active = set(self._proc_active.values())
        for key, episode in self._proc_episodes.items():
            if key not in active and episode.persisted.get(episode.source_key, -1) >= episode.last:
                del self._proc_episodes[key]
                return True
        return False

    def _proc_tick_maybe(self) -> None:
        """A bounded process-evidence pass when due; never holds the lock over probes."""
        if not self._usage_sources:
            return
        now = self._now_ms()
        if self._proc_last_tick is not None and now - self._proc_last_tick < proc_link.TICK_MS:
            return
        with self._usage_lock:
            kinds = {s.kind for s in self._usage_sources}
            by_kind: dict[str, list[UsageSource]] = {}
            for source in self._usage_sources:
                by_kind.setdefault(source.kind, []).append(source)
            eligible = sorted(
                pane_id
                for pane_id, rec in self._proc_panes.items()
                if rec.kind in kinds and rec.space is not None and not rec.has_session
            )
            context = {
                pane_id: (rec.kind or "", rec.space or "")
                for pane_id in eligible
                for rec in (self._proc_panes[pane_id],)
            }
        if not eligible:
            return  # skip entirely: no subprocess runs without an eligible pane
        self._proc_last_tick = now
        start_at = self._proc_cursor % len(eligible)
        taken = (eligible[start_at:] + eligible[:start_at])[: proc_link.PANES_PER_PASS]
        self._proc_cursor = (start_at + len(taken)) % len(eligible)
        deadline = self._proc_clock() + proc_link.PASS_WALL_S
        for pane_id in taken:
            if self._proc_clock() >= deadline:
                break
            kind, space = context[pane_id]
            targets = tuple(
                proc_link.ProcTarget(source.source_key, source.root, reader)
                for source in by_kind.get(kind, ())
                if (reader := READERS.get(source.format)) is not None
                and reader.lock_filename is not None
            )
            if not targets:
                continue
            try:
                obs = self._proc_probe(pane_id, targets, self._proc_clock, deadline)
            except Exception:  # noqa: S112 - a failed probe or read never breaks the stream
                continue
            if obs is not None:
                self._proc_apply(pane_id, kind, space, obs, now)

    def _proc_apply(
        self, pane_id: str, kind: str, space: str, obs: proc_link.ProcObservation, now: int
    ) -> None:
        """Fold one validated observation into rank-2 episodes. Takes the lock."""
        with self._usage_lock:
            rec = self._proc_panes.get(pane_id)
            if rec is None or rec.kind != kind or rec.space != space or rec.has_session:
                self._proc_close_pane(pane_id)
                return  # the pane moved on while probing; this pass is stale
            known = self._usage_spaces.get(space)
            generation = known.generation if known is not None else 0
            label = known.label if known is not None else ""
            repo_fp = self._usage_repo_fingerprint(known.repo if known is not None else None)
            seen: set[tuple[str, str]] = set()
            for target in obs.targets:
                for lock in target.locks:
                    seen.add((target.source_key, lock.session))
                    key = (pane_id, target.source_key, lock.session)
                    interval = self._proc_active.get(key)
                    episode = self._proc_episodes.get(interval or "")
                    if (
                        episode is not None
                        and episode.workspace == space
                        and episode.generation == generation
                        and episode.pid == obs.pid
                        and episode.start == obs.start
                    ):
                        episode.last = max(episode.last, now)
                        episode.label = label
                        episode.repo_fp = repo_fp
                        continue
                    if interval is not None:
                        self._proc_active.pop(key, None)  # definitive change; old window stands
                    if not self._proc_make_room():
                        self._usage_capacity_gap(kind, now)
                        continue
                    fresh = "p2:" + secrets.token_hex(16)
                    self._proc_episodes[fresh] = _ProcEpisode(
                        pane=pane_id,
                        source_key=target.source_key,
                        workspace=space,
                        kind=kind,
                        session=lock.session,
                        interval=fresh,
                        first=now,
                        last=now,
                        pid=obs.pid,
                        start=obs.start,
                        generation=generation,
                        label=label,
                        repo_fp=repo_fp,
                    )
                    self._proc_active[key] = fresh
            # Definitive absence: a probed lock no longer held deactivates, unextended.
            covered = {target.source_key for target in obs.targets}
            for key in [
                k
                for k in self._proc_active
                if k[0] == pane_id and k[1] in covered and (k[1], k[2]) not in seen
            ]:
                self._proc_active.pop(key, None)

    def _usage_capacity_gap(self, kind: str, now: int) -> None:
        gap = self._usage_gaps.get(kind)
        if gap is None:
            self._usage_gaps[kind] = _UsageGap(secrets.token_hex(16), now)
            LOG.warning("usage_mapping_gap reason=capacity")
        elif gap.last is not None:
            gap.last = None  # Conservative hull until a complete snapshot is observed.
            gap.persisted.clear()

    def _usage_observe(self, pane: dict[str, Any], now: int) -> str | None:
        """Called under _usage_lock; only exact raw identities, never path/id inference."""
        pane_id = pane.get("pane_id")
        if not isinstance(pane_id, str) or not 0 < len(pane_id) <= 256:
            return None
        old_key = self._usage_active.get(pane_id)
        old = self._usage_episodes.get(old_key or "")
        ref, space = pane.get("agent_session"), pane.get("workspace_id")
        valid = (
            isinstance(ref, dict)
            and set(ref) == {"agent", "kind", "source", "value"}
            and all(isinstance(v, str) and 0 < len(v) <= 4096 for v in ref.values())
            and ref.get("kind") in ("id", "path")
            and ref.get("agent") in {s.kind for s in self._usage_sources}
            and isinstance(space, str)
            and 0 < len(space) <= 256
        )
        if not valid:
            if old is not None:
                old.last = max(old.last, now)
            self._usage_active.pop(pane_id, None)
            self._proc_note_pane(pane, pane_id, valid=False)
            return None
        assert isinstance(ref, dict) and isinstance(space, str)  # noqa: S101
        # Readers apply this same opaque-id normalization. A path only links when
        # the reader itself reports that exact identity; no basename discovery.
        value = session_text(ref["value"])
        assert value is not None  # noqa: S101
        known = self._usage_spaces.get(space)
        generation = known.generation if known is not None else 0
        label = known.label if known is not None else ""
        repo_fp = self._usage_repo_fingerprint(known.repo if known is not None else None)
        identity = (space, generation, ref["agent"], ref["kind"], ref["source"], value)
        tab = pane.get("tab_id", old.tab if old else "")
        tab = tab if isinstance(tab, str) and len(tab) <= 256 else ""
        if old and identity == (
            old.workspace,
            old.generation,
            old.agent,
            old.ref_kind,
            old.ref_source,
            old.value,
        ):
            old.last = max(old.last, now)
            old.tab = tab
            old.label = label  # A rename flows into the live episode only.
            old.repo_fp = repo_fp
            self._proc_close_pane(pane_id)  # rank 1 owns this pane now
            self._proc_note_pane(pane, pane_id, valid=True)
            return None
        if old is not None:
            old.last = max(old.last, now)
        self._usage_active.pop(pane_id, None)
        if not self._usage_make_room():
            self._usage_capacity_gap(ref["agent"], now)
            return str(ref["agent"])  # Failed kind; snapshot may not close its uncertainty.
        key = secrets.token_hex(16)
        self._usage_episodes[key] = _UsageEpisode(
            pane=pane_id,
            workspace=space,
            agent=str(ref["agent"]),
            ref_kind=str(ref["kind"]),
            ref_source=str(ref["source"]),
            value=value,
            interval=key,
            first=now,
            last=now,
            tab=tab,
            generation=generation,
            label=label,
            repo_fp=repo_fp,
        )
        self._usage_active[pane_id] = key
        self._proc_close_pane(pane_id)  # rank 1 owns this pane now
        self._proc_note_pane(pane, pane_id, valid=True)
        return None

    def _proc_close_pane(self, pane_id: str) -> None:
        """Called under _usage_lock; deactivates the pane's rank-2 episodes.

        Windows are never extended here: an episode's `last` stays at its last
        confirmation, and attribution fallback still covers later events.
        """
        for key in [k for k in self._proc_active if k[0] == pane_id]:
            self._proc_active.pop(key, None)

    def _proc_note_pane(self, pane: dict[str, Any], pane_id: str, *, valid: bool) -> None:
        """Called under _usage_lock; retains (or drops) the probing record."""
        if (
            not valid
            and "agent_session" not in pane
            and "agent" not in pane
            and "workspace_id" not in pane
        ):
            self._proc_panes.pop(pane_id, None)
            self._proc_close_pane(pane_id)
            return
        kinds = {s.kind for s in self._usage_sources}
        kind: str | None = None
        ref = pane.get("agent_session")
        if valid and isinstance(ref, dict) and isinstance(ref.get("agent"), str):
            kind = ref["agent"]
        elif pane.get("agent") in kinds:
            kind = str(pane["agent"])
        space = pane.get("workspace_id")
        self._proc_panes[pane_id] = _ProcPane(
            kind, space if isinstance(space, str) and 0 < len(space) <= 256 else None, valid
        )

    def _usage_repo_fingerprint(self, repo: str | None) -> str | None:
        if self._usage_repo_fp is None or not isinstance(repo, str) or not repo:
            return None
        try:
            return self._usage_repo_fp(repo)
        except Exception:
            return None  # Usage evidence never breaks observation; the label still stores.

    def _usage_observe_cwd(self, pane: dict[str, Any], now: int, *, close: bool) -> None:
        """Pane-side cwd evidence for inferred linking. Called under _usage_lock; the raw folder
        is digested in memory and never stored.
        """
        if self._usage_digest is None:
            return
        pane_id = pane.get("pane_id")
        if not isinstance(pane_id, str) or not 0 < len(pane_id) <= 256:
            return
        kinds = {s.kind for s in self._usage_sources}
        kind: str | None = None
        ref = pane.get("agent_session")
        if isinstance(ref, dict) and ref.get("agent") in kinds:
            kind = str(ref["agent"])
        if kind is None and pane.get("agent") in kinds:
            kind = str(pane["agent"])
        space = pane.get("workspace_id")
        folder = pane.get("foreground_cwd")
        if not isinstance(folder, str) or not folder:
            folder = pane.get("cwd")
        old_key = self._usage_cwd_active.get(pane_id)
        old = self._usage_cwd_episodes.get(old_key or "")
        if (
            kind is None
            or not isinstance(space, str)
            or not 0 < len(space) <= 256
            or not isinstance(folder, str)
            or not 0 < len(folder) <= 4096
        ):
            if close and old is not None:
                old.last = max(old.last, now)
            if close:
                self._usage_cwd_active.pop(pane_id, None)
            return
        try:
            digest = self._usage_digest(folder)
        except Exception:
            return  # Usage evidence never breaks observation.
        if not isinstance(digest, str) or not digest:
            return
        known = self._usage_spaces.get(space)
        generation = known.generation if known is not None else 0
        identity = (digest, kind, space, generation)
        if old is not None and identity == (old.digest, old.kind, old.workspace, old.generation):
            old.last = max(old.last, now)
            return
        if old is not None:
            old.last = max(old.last, now)
        self._usage_cwd_active.pop(pane_id, None)
        if not self._usage_cwd_make_room():
            # Exhaustion drops inference only: exact linking is never affected.
            LOG.warning("usage_cwd_dropped reason=capacity")
            return
        key = secrets.token_hex(16)
        self._usage_cwd_episodes[key] = _UsagePaneDigest(
            pane=pane_id,
            digest=digest,
            kind=kind,
            workspace=space,
            generation=generation,
            interval=key,
            first=now,
            last=now,
        )
        self._usage_cwd_active[pane_id] = key

    def _usage_track_spaces(self, snapshot: dict[str, Any], *, revalidate: bool) -> None:
        """Generations for the snapshot's workspace ids. Called under _usage_lock before panes
        are observed.
        """
        seen: dict[str, tuple[str, str | None]] = {}
        workspaces = snapshot.get("workspaces")
        if not isinstance(workspaces, list):
            return  # Not a complete snapshot: never infer disappearance from it.
        for entry in workspaces:
            if not isinstance(entry, dict):
                continue
            wid = entry.get("workspace_id")
            if not isinstance(wid, str) or not 0 < len(wid) <= 256:
                continue
            label = entry.get("label")
            label = label[:128] if isinstance(label, str) else ""
            repo: str | None = None
            worktree = entry.get("worktree")
            if isinstance(worktree, dict):
                candidate = worktree.get("repo_key")
                repo = candidate if isinstance(candidate, str) and candidate else None
            seen[wid] = (label, repo)
        for wid, (label, repo) in seen.items():
            known = self._usage_spaces.get(wid)
            if known is None:
                self._usage_spaces[wid] = _UsageSpace(0, label, repo, True)
                continue
            if revalidate and known.present and (known.label, known.repo) != (label, repo):
                known.generation += 1
            known.label, known.repo, known.present = label, repo, True
        for wid, known in self._usage_spaces.items():
            if known.present and wid not in seen:
                known.generation += 1
                known.present = False

    def _usage_snapshot(self, snapshot: dict[str, Any], *, revalidate: bool = False) -> None:
        with self._usage_lock:
            if not self._usage_sources:
                return
            now = self._now_ms()
            self._usage_track_spaces(snapshot, revalidate=revalidate)
            seen: set[str] = set()
            failed: set[str] = set()
            for pane in snapshot.get("panes", []):
                if isinstance(pane, dict):
                    if isinstance(pane.get("pane_id"), str):
                        seen.add(pane["pane_id"])
                    kind = self._usage_observe(pane, now)
                    if kind is not None:
                        failed.add(kind)
                    self._usage_observe_cwd(pane, now, close=True)
            for pane_id in self._usage_active.keys() - seen:
                self._usage_observe({"pane_id": pane_id}, now)
            for pane_id in self._usage_cwd_active.keys() - seen:
                self._usage_observe_cwd({"pane_id": pane_id}, now, close=True)
            for pane_id in set(self._proc_panes) - seen:
                self._proc_panes.pop(pane_id, None)
                self._proc_close_pane(pane_id)
            for kind, gap in self._usage_gaps.items():
                if kind not in failed and gap.last is None:
                    gap.last = max(gap.first, now)
                    gap.persisted.clear()

    def _usage_event(self, name: str, data: dict[str, Any]) -> None:
        with self._usage_lock:
            if not self._usage_sources:
                return
            now = self._now_ms()
            pane = data.get("pane", data)
            if not isinstance(pane, dict):
                return
            if name == "pane.moved" and data.get("previous_pane_id") != pane.get("pane_id"):
                self._usage_observe({"pane_id": data.get("previous_pane_id")}, now)
            if name == "workspace.closed":
                wid = data.get("workspace_id")
                known = self._usage_spaces.get(wid) if isinstance(wid, str) else None
                if known is not None and known.present:
                    known.generation += 1
                    known.present = False
            if name in ("workspace.closed", "tab.closed", "tab.moved"):
                for key in tuple(self._usage_active.values()):
                    old = self._usage_episodes[key]
                    matches = (
                        (old.workspace == data.get("workspace_id"))
                        if name == "workspace.closed"
                        else (old.tab == data.get("tab_id"))
                    )
                    if not matches:
                        continue
                    record: dict[str, Any] = {"pane_id": old.pane}
                    if name == "tab.moved":
                        record.update(
                            workspace_id=data.get("workspace_id"),
                            tab_id=old.tab,
                            agent_session={
                                "agent": old.agent,
                                "kind": old.ref_kind,
                                "source": old.ref_source,
                                "value": old.value,
                            },
                        )
                    self._usage_observe(record, now)
                    if name != "tab.moved":
                        self._usage_observe_cwd({"pane_id": old.pane}, now, close=True)
                return
            if name in ("pane.closed", "pane.exited"):
                self._usage_observe({"pane_id": _pane_id_from_event(data)}, now)
                self._usage_observe_cwd({"pane_id": _pane_id_from_event(data)}, now, close=True)
            elif name.startswith("pane.") and "agent_session" in pane:
                self._usage_observe(pane, now)
                self._usage_observe_cwd(pane, now, close=False)
            elif name == "pane.moved":
                # Partial move events have no session ref: retain only a previously
                # observed identity, and create a fresh interval in the new space.
                for key in tuple(self._usage_active.values()):
                    old = self._usage_episodes[key]
                    if old.pane == _pane_id_from_event(data):
                        self._usage_observe(
                            {
                                "pane_id": old.pane,
                                "workspace_id": pane.get("workspace_id"),
                                "agent_session": {
                                    "agent": old.agent,
                                    "kind": old.ref_kind,
                                    "source": old.ref_source,
                                    "value": old.value,
                                },
                            },
                            now,
                        )

    # -- LinkSource port -------------------------------------------------

    @property
    def dispatch_adapter(self) -> DispatchAdapter:
        return self

    def snapshot_document(self, host_id: str) -> dict[str, Any]:
        if self._snapshot is None:
            raise HerdrProtocolError("not_bootstrapped", "bootstrap before snapshot")
        try:
            from relaydeck.connector.catalog import discover_agent_catalog

            doc = translate_snapshot(host_id, self.epoch, self._snapshot, self._agents)
            doc["host"]["agent_catalog"] = discover_agent_catalog(
                self._now_ms(), kinds=self._agent_kinds, programs=self._launch_programs
            )
            return doc
        except ValueError as exc:
            raise HerdrProtocolError("bad_snapshot", str(exc)) from exc

    def drain_outputs(self) -> list[tuple[str, str, bytes]]:
        return [(frame.terminal_id, frame.epoch, frame.data) for frame in self._relay.drain()]

    def take_output_batch(self) -> list[tuple[str, str, bytes]]:
        return [
            (frame.terminal_id, frame.epoch, frame.data)
            for frame in self._relay.drain(reserve=True)
        ]

    def release_output_batch(self, size: int) -> None:
        self._relay.release(size)
        # Room again: a PTY held back by backpressure may be read now.
        self._wake_output_readers()

    def drain_terminal_resets(self) -> dict[str, str]:
        resets, self._terminal_resets = self._terminal_resets, {}
        # The reset is on its way to the gateway: bytes may be staged again (old-epoch frames
        # are fenced there until the rotated epoch arrives).
        self._relay.clear_overflow(resets)
        return resets

    def _stage(self, terminal_id: str, epoch: str, data: bytes) -> int:
        """Stage PTY bytes. A terminal that overflows the relay is a per-terminal condition: its
        staged frames are dropped and a resync (`terminal.reset_required`) is requested; the
        link, every other terminal and the UI keep running.
        """
        staged = self._relay.script(terminal_id, epoch, data)
        if staged == 0 and terminal_id not in self._terminal_resets:
            self._terminal_resets[terminal_id] = epoch
            LOG.warning(
                "terminal relay overflow, resync requested (terminal=%s overflows=%d load=%d)",
                terminal_id,
                self._relay.overflow_count,
                self._relay.load,
            )
        return staged

    # -- DispatchAdapter (delegated; shapes verified in-schema) ----------

    async def agent_prompt(self, target: str, text: str) -> dict[str, Any]:
        # Herdr resolves agent targets by pane id (terminal ids answer
        # agent_not_found); the gateway names agents by terminal id.
        return await self._adapter.agent_prompt(self.agent_attach_target(target), text)

    async def tab_create(self, workspace_id: str, *, label: str | None = None) -> dict[str, Any]:
        return await self._adapter.tab_create(workspace_id, label=label)

    def verify_agent_kind(self, kind: str) -> None:
        from relaydeck.connector.catalog import discover_agent_catalog
        from relaydeck.contracts.catalog import catalog_from_wire

        catalog = catalog_from_wire(
            discover_agent_catalog(
                self._now_ms(), kinds=self._agent_kinds, programs=self._launch_programs
            )
        )
        if catalog is None or kind not in catalog.available:
            raise HerdrError("agent_kind_unavailable", "agent kind is unavailable on this host")

    async def workspace_create(
        self, *, cwd: str | None = None, label: str | None = None
    ) -> dict[str, Any]:
        return await self._adapter.workspace_create(cwd=cwd, label=label)

    async def tab_rename(self, tab_id: str, label: str) -> dict[str, Any]:
        try:
            return await self._adapter.tab_rename(tab_id, label)
        finally:
            self._snapshot_dirty = True

    async def agent_list(self) -> list[dict[str, Any]]:
        return await self._adapter.agent_list()

    async def agent_rename(self, target: str, name: str | None) -> dict[str, Any]:
        try:
            agents = await self._adapter.agent_list()
            matches = [
                row for row in agents if isinstance(row, dict) and row.get("terminal_id") == target
            ]
            if not matches:
                raise HerdrError("agent_not_found", "agent is no longer listed")
            if len(matches) != 1:
                raise HerdrProtocolError("bad_agent_result", "ambiguous agent identity")
            pane = matches[0].get("pane_id")
            if not isinstance(pane, str) or not 1 <= len(pane) <= 128:
                raise HerdrProtocolError("bad_agent_result", "agent pane unavailable")
            return await self._adapter.agent_rename(pane, name)
        finally:
            self._snapshot_dirty = True

    async def agent_start(
        self,
        name: str,
        kind: str,
        pane_id: str,
        args: list[str] | None = None,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        return await self._adapter.agent_start(
            name, kind, pane_id, args=args, timeout_ms=timeout_ms
        )

    async def agent_revision(self, target: str) -> int | None:
        """Fresh upstream agent.list assertion check, never a new protocol method."""
        agents = await self._adapter.agent_list()
        for agent in agents:
            if agent.get("terminal_id") == target:
                revision = agent.get("revision")
                return revision if type(revision) is int and revision >= 0 else None
        return None

    async def pane_close(self, pane_id: str) -> dict[str, Any]:
        return await self._adapter.pane_close(pane_id)

    async def tab_close(self, tab_id: str) -> dict[str, Any]:
        return await self._adapter.tab_close(tab_id)

    # -- bootstrap / metadata --------------------------------------------

    @property
    def epoch(self) -> str:
        return f"{BOOT_EPOCH_PREFIX}-{EXPECTED_PROTOCOL}-{self._boots}-{self._boot_tag}"

    @property
    def bootstrapped(self) -> bool:
        return self._snapshot is not None

    @property
    def skipped_events(self) -> int:
        return self._skipped

    def _now_ms(self) -> int:
        if self._clock is not None:
            return self._clock()
        import time as _time

        return int(_time.time() * 1000)

    def _stamp(self) -> str:
        from datetime import UTC, datetime

        return datetime.fromtimestamp(self._now_ms() / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    async def bootstrap(self) -> BootstrapData:
        """Handshake + subscribe + snapshot + buffered-event translation. Buffered
        (bootstrap-window) events become seed partials; live events after handoff are
        consumed by `collect_live`.
        """
        result = await self._adapter.bootstrap(self._subscriptions)
        previous, self._subscription = self._subscription, result.subscription
        try:
            await self._metadata_reads.close()
        finally:
            if previous is not None:
                await previous.aclose()
        inner = result.snapshot.get("snapshot", {})
        if not isinstance(inner, dict):
            raise HerdrProtocolError("bad_snapshot", "session.snapshot has no snapshot")
        self._snapshot = inner
        self._usage_snapshot(inner, revalidate=True)
        self._agents = list(result.agents)
        self._boots += 1
        self._pane_want = self._snapshot_pane_ids()
        self._pane_dirty = True
        await self._ensure_pane_subscriptions()
        seeds: list[dict[str, Any]] = []
        skipped = 0
        for name, data in result.buffered:
            self._usage_event(wire_event_name(name), data)
            self._event_seq += 1
            partial = translate_event(
                wire_event_name(name),
                data,
                epoch=self.epoch,
                event_id=f"herdr-{self._boots}-{self._event_seq}",
                observed_at=self._stamp(),
            )
            if partial is None:
                skipped += 1
            else:
                seeds.append(partial)
        self._seeds = seeds
        self._skipped += skipped
        return BootstrapData(
            epoch=self.epoch,
            seed_events=tuple(seeds),
            buffered_total=len(result.buffered),
            skipped_events=skipped,
        )

    def seed_events(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._seeds)

    async def refresh(self) -> None:
        """Re-read snapshot + agents without a new continuity boundary
        (same epoch): content refresh, not a reconnection."""
        if not self.bootstrapped:
            raise HerdrProtocolError("not_bootstrapped", "bootstrap before refresh")
        snapshot = await self._adapter.session_snapshot()
        inner = snapshot.get("snapshot", {})
        if not isinstance(inner, dict):
            raise HerdrProtocolError("bad_snapshot", "session.snapshot has no snapshot")
        agents = await self._adapter.agent_list()
        self._snapshot, self._agents = inner, agents
        self._usage_snapshot(inner)
        fresh = self._snapshot_pane_ids()
        if fresh != self._pane_want:
            self._pane_want = fresh
            self._pane_dirty = True

    # -- live relay (LiveSource port) --------------------------------------

    def _snapshot_pane_ids(self) -> set[str]:
        panes: set[str] = set()
        if isinstance(self._snapshot, dict):
            for entry in self._snapshot.get("panes") or []:
                if isinstance(entry, dict) and entry.get("pane_id"):
                    panes.add(str(entry["pane_id"]))
        return panes

    @property
    def pane_subscribed(self) -> frozenset[str]:
        """Pane ids currently covered by the per-pane subscription."""
        return self._pane_have

    async def _ensure_pane_subscriptions(self) -> None:
        """Sync the per-pane `agent_status_changed` subscription with the
        wanted pane set. Never raises: failures keep the previous
        subscription (if any) and retry with backoff."""
        if not self._pane_dirty and self._pane_subscription is not None:
            return
        want = frozenset(self._pane_want)
        if want == self._pane_have and self._pane_subscription is not None:
            self._pane_dirty = False
            return
        if not want:
            if self._pane_have:
                LOG.info("herdr_pane_subscriptions n=0")
            await self._drop_pane_subscription()
            self._pane_dirty = False
            return
        now_ms = self._now_ms()
        if now_ms < self._pane_retry_after_ms:
            return
        entries = [{"type": "pane.agent_status_changed", "pane_id": pid} for pid in sorted(want)]
        try:
            fresh = await self._adapter.open_subscription(entries)
        except Exception as exc:  # noqa: BLE001 - freshness degrades, link survives
            LOG.warning(
                "per-pane subscribe failed (%d panes), retrying later: %s",
                len(want),
                error_label(exc),
            )
            self._schedule_pane_retry(now_ms)
            return
        await self._drop_pane_subscription()
        self._pane_subscription = fresh
        self._pane_have = want
        self._pane_dirty = False
        # counts only, so a stale dock can be traced to the subscription set.
        LOG.info("herdr_pane_subscriptions n=%d", len(want))

    def _schedule_pane_retry(self, now_ms: int) -> None:
        """Arm the bounded, jittered per-pane re-subscribe backoff."""
        delay = _PANE_SUB_RETRY_MS + self._rng.randrange(_PANE_SUB_RETRY_JITTER_MS + 1)
        self._pane_retry_after_ms = now_ms + delay

    async def _drop_pane_subscription(self) -> None:
        current, self._pane_subscription = self._pane_subscription, None
        self._pane_have = frozenset()
        if current is not None:
            await current.aclose()

    async def sync_pane_subscriptions(self) -> frozenset[str]:
        """Force a per-pane subscription sync (tests + explicit callers)."""
        self._pane_dirty = True
        await self._ensure_pane_subscriptions()
        return self._pane_have

    def _track_panes(self, name: str, data: dict[str, Any]) -> None:
        """Fold pane-membership live events into the wanted pane set."""
        if name == "pane.created":
            pane_id = _pane_id_from_event(data)
            if pane_id is not None and pane_id not in self._pane_want:
                self._pane_want.add(pane_id)
                self._pane_dirty = True
        elif name in ("pane.closed", "pane.exited"):
            pane_id = _pane_id_from_event(data)
            if pane_id is not None and pane_id in self._pane_want:
                self._pane_want.discard(pane_id)
                self._pane_dirty = True

    def _consume_live_line(self, name: str, data: dict[str, Any]) -> dict[str, Any] | None:
        self._event_seq += 1
        name = wire_event_name(name)
        self._usage_event(name, data)
        self._track_panes(name, data)
        if name == "layout.updated":
            self.reassert_geometry()
        elif name == "pane.updated":
            pane_id = _pane_id_from_event(data)
            for terminal_id in list(self._geometry):
                if pane_id is not None and self._pane_matches(terminal_id, pane_id):
                    self.reassert_geometry(terminal_id)
        if name in REMOVAL_EVENTS:
            self._snapshot_dirty = True
        partial = translate_event(
            name,
            data,
            epoch=self.epoch,
            event_id=f"herdr-{self._boots}-{self._event_seq}",
            observed_at=self._stamp(),
        )
        if partial is None:
            self._skipped += 1
            self._note_skipped()
            return None
        return partial

    def _note_skipped(self) -> None:
        """Counter log only: the count, never a payload. At most once per SKIP_LOG_INTERVAL_S."""
        now = self._retry_clock()
        if now - self._skip_logged_at >= SKIP_LOG_INTERVAL_S:
            self._skip_logged_at = now
            LOG.info("herdr_events_skipped n=%d", self._skipped)

    async def wait_metadata_ready(self) -> None:
        """Wait for a held stream or the next per-pane subscription retry."""
        if self._subscription is None or not self.bootstrapped:
            raise HerdrProtocolError("not_bootstrapped", "bootstrap before waiting")
        try:
            while True:
                await self._ensure_pane_subscriptions()
                subs = [s for s in (self._subscription, self._pane_subscription) if s is not None]
                delay = (
                    max(0.001, (self._pane_retry_after_ms - self._now_ms()) / 1000)
                    if self._pane_dirty and self._pane_want
                    else None
                )
                if await self._metadata_reads.wait(subs, delay):
                    return
        finally:
            # Completed reads remain owned until collect_live consumes them.
            await self._metadata_reads.cancel_pending()

    def metadata_requires_snapshot(self, events: list[dict[str, Any]]) -> bool:
        """Only a status delta for a known agent's pane is complete without a refresh."""
        panes = {row.get("pane_id") for row in self._agents if isinstance(row, dict)}
        return any(
            event.get("type") != "pane.upserted"
            or set(event.get("payload", {})) != {"agent_status"}
            or event.get("resource_id") not in panes
            for event in events
        )

    async def metadata_deltas(
        self, events: list[dict[str, Any]], host_id: str
    ) -> list[dict[str, Any]] | None:
        """Read only changed agents through the pinned agent.get / agent_info contract."""
        if self.metadata_requires_snapshot(events):
            return None
        changed = dict.fromkeys(str(event["resource_id"]) for event in events)
        staged: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
        for pane in changed:
            matches = [
                (i, row)
                for i, row in enumerate(self._agents)
                if isinstance(row, dict) and row.get("pane_id") == pane
            ]
            if len(matches) != 1:
                return None
            index, old = matches[0]
            try:
                result = await self._adapter.agent_get(str(old["terminal_id"]))
            except HerdrError:
                return None
            agent = result.get("agent")
            if (
                result.get("type") != "agent_info"
                or not isinstance(agent, dict)
                or any(
                    agent.get(key) != old.get(key)
                    for key in ("terminal_id", "pane_id", "workspace_id", "tab_id")
                )
                or type(agent.get("revision")) is not int
                or agent["revision"] < old.get("revision", 0)
                or agent.get("agent_status") not in AGENT_STATUSES
            ):
                return None
            row = _agent_row(host_id, agent)
            staged.append(
                (
                    index,
                    agent,
                    row,
                )
            )
        for index, agent, _event in staged:
            self._agents[index] = agent
        rows = {agent["pane_id"]: row for _index, agent, row in staged}
        # Retain every transition in stream order, even when several share a target lookup.
        return [
            {
                **event,
                "type": "agent.upserted",
                "resource_id": rows[event["resource_id"]]["terminal_id"],
                "resource_revision": rows[event["resource_id"]]["revision"],
                "payload": {
                    **rows[event["resource_id"]],
                    "status": _status(event["payload"]["agent_status"]),
                    "status_changed": True,
                },
            }
            for event in events
        ]

    async def collect_live(self, timeout: float) -> list[dict[str, Any]]:
        """Drain translated live events from the held subscriptions. Multiplexes the global and
        per-pane subscriptions within `timeout` seconds (empty list when quiet).
        """
        if self._subscription is None or not self.bootstrapped:
            raise HerdrProtocolError("not_bootstrapped", "bootstrap before collect_live")
        await self._ensure_pane_subscriptions()
        partials: list[dict[str, Any]] = []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout)
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            subs = [sub for sub in (self._subscription, self._pane_subscription) if sub is not None]
            if not await self._metadata_reads.wait(subs, remaining if not partials else 0):
                break
            for sub in subs:
                task = self._metadata_reads.tasks.get(sub)
                if task is None or not task.done():
                    continue
                self._metadata_reads.tasks.pop(sub)
                try:
                    name, data = task.result()
                except StopAsyncIteration:
                    if sub is self._subscription:
                        raise HerdrProtocolError(
                            "upstream_closed", "daemon closed subscription"
                        ) from None
                    LOG.warning("per-pane subscription EOF, re-opening later")
                    await self._drop_pane_subscription()
                    self._pane_dirty = True
                    self._schedule_pane_retry(self._now_ms())
                    continue
                except HerdrProtocolError as exc:
                    if exc.code != "bad_event":
                        raise
                    self._event_seq += 1
                    self._skipped += 1
                    continue
                partial = self._consume_live_line(name, data)
                if partial is not None:
                    partials.append(partial)
        if self._pane_dirty:
            await self._ensure_pane_subscriptions()
        return partials

    def take_snapshot_dirty(self) -> bool:
        """True once after an upstream removal event, so the link re-reads
        and re-pushes the snapshot even when no live event translated."""
        dirty, self._snapshot_dirty = self._snapshot_dirty, False
        return dirty

    def drain_seeds(self) -> list[dict[str, Any]]:
        """Consume bootstrap seed partials exactly once."""
        seeds, self._seeds = self._seeds, []
        return seeds

    async def resync(self) -> None:
        """LiveSource resync: same-epoch content refresh (reconnects)."""
        await self.refresh()

    async def rebootstrap(self) -> str:
        """LiveSource rebootstrap: sweep attach sessions (attach clients do not survive daemon
        death), drop dead subscriptions, and bootstrap fresh (new continuity epoch). Gateway
        epochs persist: the next `note_epochs` respawns each mapped terminal.
        """
        self._drop_all_attached()
        await self._drop_pane_subscription()
        data = await self.bootstrap()
        return data.epoch

    def _drop_all_attached(self) -> None:
        """Upstream-death sweep: close every attach session loudly.
        Epochs persist (routing is gateway state); sessions do not."""
        if not self._attached:
            return
        LOG.warning(
            "upstream rebootstrap dropping %d attach session(s); "
            "epochs kept, next epochs resubscribe",
            len(self._attached),
        )
        for key in list(self._attached):
            try:
                self._pool.close(key)
                self._observer_pool.close(key)
            finally:
                self._attached.pop(key, None)
        self._observe_started.clear()

    def _epoch_for(self, terminal_id: str) -> str:
        return self._epochs.get(terminal_id, "")

    def enable_async_attachments(self) -> None:
        if self._attach_io is None:
            self._attach_io = AttachLifecycle(self._wake_output_readers)
            for session, _, _ in self._attached.values():
                session.lifecycle = self._attach_io

    async def wait_attachments(self, terminal_id: str, epoch: str) -> None:
        """Wait only for this route's pending spawn, never unrelated process reaping."""
        kind = self._epoch_kinds.get(terminal_id)
        held = self._attached.get(f"{kind}:{terminal_id}")
        if held is not None and self._epoch_for(terminal_id) == epoch:
            session = held[0]
            ready = getattr(session, "_spawn_done", None)
            if getattr(session, "spawn_pending", False) and ready is not None:
                await ready.wait()

    def note_epochs(self, routes: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
        """Apply the gateway epoch table and auto-attach newly mapped
        terminals (shell kind without takeover, agent kind with the
        explicit-takeover agent planner). Never raises: per-terminal
        failures are returned for the link to log loudly."""
        failures: dict[str, str] = {}
        for terminal_id in set(self._epochs) - set(routes):
            self.drop_terminal(terminal_id)
        for terminal_id, route in routes.items():
            epoch = route.get("epoch", "")
            kind = route.get("kind", "")
            mode = route.get("mode", "control")
            if not terminal_id or not epoch:
                failures[str(terminal_id)] = "empty terminal or epoch"
                continue
            if kind not in ("shell", "agent"):
                failures[terminal_id] = f"unknown kind {kind!r}"
                continue
            if mode not in ("control", "observe"):
                failures[terminal_id] = "unknown upstream mode"
                continue
            if terminal_id in self._epochs and (
                self._epochs[terminal_id] != epoch
                or self._epoch_kinds.get(terminal_id) != kind
                or self._epoch_modes.get(terminal_id, "control") != mode
            ):
                # A gateway reset rotates epochs after a real observer death.
                # That automatic rotation must not erase its retry budget.
                retry = self._observe_retries.get(terminal_id)
                issue = self._observe_issues.get(terminal_id)
                keep_retry = self._epoch_modes.get(terminal_id) == mode == "observe"
                self.drop_terminal(terminal_id)
                if keep_retry:
                    if retry is not None:
                        self._observe_retries[terminal_id] = retry
                    if issue is not None:
                        self._observe_issues[terminal_id] = issue
            self._epochs[terminal_id] = epoch
            self._epoch_kinds[terminal_id] = kind
            self._epoch_modes[terminal_id] = mode
            key = f"{kind}:{terminal_id}"
            held = self._attached.get(key)
            if held is not None and held[0].running:
                if mode == "observe":
                    self._observe_stable(terminal_id)
                continue
            retry = self._observe_retries.get(terminal_id)
            if mode == "observe" and retry is not None and retry.deadline_ms is not None:
                if self._observe_now_ms() < retry.deadline_ms:
                    continue
            if mode == "control" and self._attach_refused.get(terminal_id) == epoch:
                failures[terminal_id] = "attach refused upstream; explicit Take over required"
                continue
            if kind == "agent" and mode == "control":
                if self._agent_lost.get(terminal_id) == epoch:
                    failures[terminal_id] = "agent attach ended; explicit Take over required"
                    continue
                self._agent_lost.pop(terminal_id, None)
                if held is not None:
                    # Displaced or exited under the same epoch: relay the last
                    # bytes and reap, but never take the pane back silently.
                    self._drain_dead(held[0], terminal_id, epoch)
                    self._reap_dead(key, terminal_id, held[0].exit_status)
                    self._agent_lost[terminal_id] = epoch
                    failures[terminal_id] = "agent attach ended; explicit Take over required"
                    continue
            try:
                if held is not None:
                    # A session that died while epoch-unmapped is still held: relay its last bytes
                    # under the gateway epoch, then reap so a fresh attach respawns. No input is
                    # replayed.
                    self._drain_dead(held[0], terminal_id, epoch)
                    self._reap_dead(key, terminal_id, held[0].exit_status)
                    if mode == "observe":
                        continue  # One timed retry, never immediate respawn.
                if mode == "observe":
                    self.observe_terminal(terminal_id, kind)
                elif kind == "shell":
                    self.attach_shell(terminal_id, takeover=route.get("takeover") is True)
                else:
                    self.attach_agent(terminal_id)
            except Exception as exc:  # noqa: BLE001 - reported, never raised
                if mode == "observe":
                    reason = "capacity" if isinstance(exc, PoolFullError) else "spawn_failed"
                    self._schedule_observe_retry(terminal_id, reason)
                    if self._observe_issue(terminal_id, reason):
                        failures[terminal_id] = (
                            "PoolFullError: observer capacity deferred"
                            if reason == "capacity"
                            else "observer spawn failed; timed retry scheduled"
                        )
                else:
                    failures[terminal_id] = error_label(exc)
        return failures

    def drop_terminal(self, terminal_id: str) -> None:
        """An authoritative table removal detaches only the local PTY tap."""
        self._wake_output_readers()
        observers_before = len(self._observer_pool)
        self._agent_lost.pop(terminal_id, None)
        for kind in ("shell", "agent"):
            key = f"{kind}:{terminal_id}"
            try:
                self._pool.close(key)
                self._observer_pool.close(key)
            finally:
                self._attached.pop(key, None)
        self._observe_started.pop(terminal_id, None)
        self._observe_retries.pop(terminal_id, None)
        self._observe_issues.pop(terminal_id, None)
        self._resize_pending.pop(terminal_id, None)
        timer = self._resize_timers.pop(terminal_id, None)
        if timer is not None:
            timer.cancel()
        self._geometry.pop(terminal_id, None)
        self._reasserted_at.pop(terminal_id, None)
        self._attach_refused.pop(terminal_id, None)
        self._cancel_correction(terminal_id)
        if len(self._observer_pool) < observers_before:
            self._wake_capacity_retries()
        self._epochs.pop(terminal_id, None)
        self._epoch_kinds.pop(terminal_id, None)
        self._epoch_modes.pop(terminal_id, None)

    def _observe_now_ms(self) -> int:
        return int(self._retry_clock() * 1000)

    def _observe_issue(
        self, terminal_id: str, reason: str, status: int | None = None, category: str = "empty"
    ) -> bool:
        issue = (reason, status, category)
        changed = self._observe_issues.get(terminal_id) != issue
        self._observe_issues[terminal_id] = issue
        return changed

    def _observe_stable(self, terminal_id: str) -> None:
        started = self._observe_started.get(terminal_id)
        if started is not None and self._observe_now_ms() - started >= _OBSERVE_STABLE_MS:
            self._observe_retries.pop(terminal_id, None)
            self._observe_issues.pop(terminal_id, None)

    def _schedule_observe_retry(self, terminal_id: str, reason: str) -> None:
        self._observe_stable(terminal_id)
        previous = self._observe_retries.get(terminal_id)
        attempt = previous.attempt + 1 if previous is not None else 1
        delay = _OBSERVE_RETRY_DELAYS_MS[min(attempt - 1, len(_OBSERVE_RETRY_DELAYS_MS) - 1)]
        self._observe_retries[terminal_id] = _ObserveRetry(
            attempt, self._observe_now_ms() + delay, reason
        )
        self._observe_started.pop(terminal_id, None)
        self._wake_output_readers()

    def _wake_capacity_retries(self) -> None:
        for retry in self._observe_retries.values():
            if retry.reason == "capacity" and retry.deadline_ms is not None:
                retry.deadline_ms = self._observe_now_ms()

    def _retry_due_observers(self) -> None:
        for terminal_id, retry in list(self._observe_retries.items()):
            if (
                retry.deadline_ms is None
                or retry.deadline_ms > self._observe_now_ms()
                or self._epoch_modes.get(terminal_id) != "observe"
                or terminal_id not in self._epochs
            ):
                continue
            kind = self._epoch_kinds[terminal_id]
            if f"{kind}:{terminal_id}" in self._attached:
                continue
            try:
                self.observe_terminal(terminal_id, kind)
            except Exception as exc:  # noqa: BLE001 - fixed categories, never exception text
                reason = "capacity" if isinstance(exc, PoolFullError) else "spawn_failed"
                self._schedule_observe_retry(terminal_id, reason)
                if self._observe_issue(terminal_id, reason):
                    LOG.warning("observer deferred (terminal=%s reason=%s)", terminal_id, reason)

    async def submit_terminal_input(
        self, terminal_id: str, epoch: str, kind: str, data: bytes
    ) -> str | None:
        session = self._input_session(terminal_id, epoch)
        if session is None:
            return "stale or unavailable terminal"
        try:
            if kind == "text":
                await session.write_async(data)
            elif kind == "key":
                # Legacy named-key frames use Herdr's tested encoder. Never
                # write a combo name as literal terminal text or guess bytes.
                panes = {
                    str(row["pane_id"])
                    for row in self._agents
                    if row.get("terminal_id") == terminal_id and row.get("pane_id")
                }
                if not panes and self._snapshot is not None:
                    panes = {
                        str(row["pane_id"])
                        for row in self._snapshot.get("panes", [])
                        if row.get("pane_id")
                        and (row.get("pane_id") == terminal_id or row.get("tab_id") == terminal_id)
                    }
                if len(panes) != 1:
                    return "named-key pane identity unavailable"
                await self._adapter.pane_send_keys(next(iter(panes)), [data.decode("utf-8")])
            else:
                return "unknown input kind"
        except (OSError, TimeoutError, HerdrError, RuntimeError) as exc:
            return f"input outcome uncertain: {type(exc).__name__}"
        return None

    def resize_terminal(self, terminal_id: str, epoch: str, cols: int, rows: int) -> str | None:
        """Apply a controller resize. The latest request wins and is applied once requests
        settle (RESIZE_SETTLE_S), so twenty rapid resume/rotate events replay the screen
        once.
        """
        session = self._input_session(terminal_id, epoch)
        if session is None:
            return "stale or unavailable terminal"
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return self._apply_resize(terminal_id, epoch, cols, rows)
        self._resize_pending[terminal_id] = (epoch, cols, rows)
        timer = self._resize_timers.pop(terminal_id, None)
        if timer is not None:
            timer.cancel()
        self._resize_timers[terminal_id] = loop.call_later(
            RESIZE_SETTLE_S, self._flush_resize, terminal_id
        )
        return None

    def _flush_resize(self, terminal_id: str) -> None:
        self._resize_timers.pop(terminal_id, None)
        pending = self._resize_pending.pop(terminal_id, None)
        if pending is None:
            return
        reason = self._apply_resize(terminal_id, *pending)
        if reason is not None:
            LOG.warning("terminal resize not applied (terminal=%s): %s", terminal_id, reason)

    def _apply_resize(self, terminal_id: str, epoch: str, cols: int, rows: int) -> str | None:
        session = self._input_session(terminal_id, epoch)
        if session is None:
            return "stale or unavailable terminal"
        try:
            session.resize(cols, rows)
        except (OSError, RuntimeError) as exc:
            return f"resize outcome uncertain: {type(exc).__name__}"
        self._note_geometry(terminal_id, session)
        return None

    def reassert_geometry(self, terminal_id: str | None = None, *, force: bool = False) -> int:
        """Re-send the last applied size to controlled terminals whose frames report another
        size (`force` skips that check). One resize per terminal per GEOMETRY_REASSERT_S, no
        output; a mismatch seen inside the cooldown is corrected by a timer that rechecks
        authority and viewport when it fires.
        """
        sent = 0
        now = self._retry_clock()
        for candidate, wanted in list(self._geometry.items()):
            if terminal_id is not None and candidate != terminal_id:
                continue
            session = self._input_session(candidate, self._epochs.get(candidate, ""))
            if not isinstance(session, ControlSession):
                continue
            if not force and session.viewport in (None, wanted):
                continue
            remaining = GEOMETRY_REASSERT_S - (now - self._reasserted_at.get(candidate, -1e9))
            if remaining > 1e-3:
                self._arm_correction(candidate, remaining)
                continue
            try:
                session.resize(*wanted)
            except (OSError, RuntimeError):
                continue
            self._reasserted_at[candidate] = now
            self._cancel_correction(candidate)
            sent += 1
            LOG.info("pane_geometry_reasserted cols=%s rows=%s", wanted[0], wanted[1])
        return sent

    def _arm_correction(self, terminal_id: str, delay: float) -> None:
        if terminal_id in self._correction_timers:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._correction_timers[terminal_id] = loop.call_later(
            delay, self._correction_due, terminal_id
        )

    def _correction_due(self, terminal_id: str) -> None:
        self._correction_timers.pop(terminal_id, None)
        self.reassert_geometry(terminal_id)

    def _cancel_correction(self, terminal_id: str) -> None:
        timer = self._correction_timers.pop(terminal_id, None)
        if timer is not None:
            timer.cancel()

    def _pane_matches(self, terminal_id: str, pane_id: str) -> bool:
        """Whether a pane event concerns this terminal (agent by pane id, shell by its pane's
        terminal id in the snapshot)."""
        if self.agent_attach_target(terminal_id) == pane_id:
            return True
        panes = self._snapshot.get("panes") if isinstance(self._snapshot, dict) else None
        for row in panes or []:
            if isinstance(row, dict) and row.get("pane_id") == pane_id:
                return row.get("terminal_id") == self.shell_attach_target(terminal_id)
        return False

    def _input_session(self, terminal_id: str, epoch: str) -> AttachSession | None:
        if self._epochs.get(terminal_id) != epoch:
            return None
        if self._epoch_modes.get(terminal_id, "control") != "control":
            return None
        kind = self._epoch_kinds.get(terminal_id)
        held = self._attached.get(f"{kind}:{terminal_id}")
        return (
            held[0]
            if held is not None and held[0].running and not getattr(held[0], "spawn_pending", False)
            else None
        )

    def pump_live(self) -> int:
        """Pump attached PTY bytes through the gateway epoch table."""
        return self.pump_attached(self._epoch_for, timeout=0.0)

    def _wake_output_readers(self) -> None:
        """Remove owned registrations BEFORE a PTY closes or a pool evicts it.

        A later wait cleanup must never unregister a reused descriptor.
        """
        self._output_changed.set()
        if self._cancel_output_wait is not None:
            self._cancel_output_wait()

    async def wait_output_ready(self) -> None:
        """Wake on mapped PTY readability or attachment/table changes. Registrations live only
        for this wait and are removed on cancellation.
        """
        if self._output_changed.is_set():
            self._output_changed.clear()
            self._retry_due_observers()
            return
        if any(
            isinstance(session, ObserveSession) and session.buffered_output
            for session, terminal, _ in self._attached.values()
            if self._epoch_for(terminal)
        ):
            self._retry_due_observers()
            return
        loop = asyncio.get_running_loop()
        ready: asyncio.Future[None] = loop.create_future()
        fds: set[int] = set()

        def notify() -> None:
            if not ready.done():
                ready.set_result(None)

        def unregister() -> None:
            for fd in fds:
                loop.remove_reader(fd)
            fds.clear()
            notify()

        async def wait_changed() -> None:
            await self._output_changed.wait()

        changed = asyncio.create_task(wait_changed())
        self._cancel_output_wait = unregister
        deadlines = [
            retry.deadline_ms
            for terminal, retry in self._observe_retries.items()
            if retry.deadline_ms is not None
            and self._epoch_modes.get(terminal) == "observe"
            and f"{self._epoch_kinds.get(terminal)}:{terminal}" not in self._attached
        ]
        timeout = max(0, min(deadlines) - self._observe_now_ms()) / 1000 if deadlines else None
        try:
            for session, terminal_id, _ in self._attached.values():
                if getattr(session, "spawn_pending", False) or not self._epoch_for(terminal_id):
                    continue
                fd = session.fileno()
                loop.add_reader(fd, notify)
                fds.add(fd)
                diagnostic_fd = session.diagnostic_fileno()
                if diagnostic_fd is not None:
                    loop.add_reader(diagnostic_fd, notify)
                    fds.add(diagnostic_fd)
            await asyncio.wait(
                {ready, changed}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            unregister()
            if self._cancel_output_wait is unregister:
                self._cancel_output_wait = None
            ready.cancel()
            changed.cancel()
            await asyncio.gather(changed, return_exceptions=True)
        # Recheck deadlines, mode and epoch after removing FD registrations so busy output cannot
        # starve retries.
        self._retry_due_observers()

    def attached_terminals(self) -> tuple[str, ...]:
        """Terminal ids with a live attach session (tests + ops)."""
        return tuple(sorted({terminal for _, (_, terminal, _) in self._attached.items()}))

    # -- terminal relay ---------------------------------------------------

    def script_output(self, terminal_id: str, epoch: str, data: bytes) -> int:
        """Stage exact bytes for relay (tests + explicit producers)."""
        staged = self._stage(terminal_id, epoch, data)
        self._output_changed.set()
        return staged

    def pane_folder(self, terminal_id: str) -> tuple[str, str, str] | None:
        """(epoch, pane id, folder) of an agent's pane, foreground folder first; call on the
        source's loop. The folder is host-local and must never be sent or logged."""
        if not isinstance(self._snapshot, dict):
            return None
        for row in self._snapshot.get("panes") or []:
            if not isinstance(row, dict) or row.get("terminal_id") != terminal_id:
                continue
            pane_id = row.get("pane_id")
            folder = row.get("foreground_cwd") or row.get("cwd")
            if isinstance(pane_id, str) and isinstance(folder, str) and 0 < len(folder) <= 4096:
                return (self.epoch, pane_id, folder)
            return None
        return None

    def shell_attach_target(self, terminal_id: str) -> str:
        """`herdr terminal attach <TERMINAL_ID>` needs a real terminal id (`term_...`). The
        inventory names shells by tab (or pane) id, so map through the snapshot panes; prefer
        the focused pane of a multi-pane tab.
        """
        if not isinstance(self._snapshot, dict):
            return terminal_id
        matches = [
            row
            for row in self._snapshot.get("panes") or []
            if isinstance(row, dict)
            and isinstance(row.get("terminal_id"), str)
            and terminal_id in (row.get("tab_id"), row.get("pane_id"), row.get("terminal_id"))
        ]
        if not matches:
            return terminal_id
        focused = [row for row in matches if row.get("focused")]
        return str((focused or matches)[0]["terminal_id"])

    def _tool(self) -> str:
        """The Herdr executable for a subprocess, or `ToolUnavailable` when the companion has no
        trusted one: the inventory over the socket still works, attaching does not."""
        if not self._binary:
            raise ToolUnavailable
        return self._binary

    def observe_terminal(self, terminal_id: str, kind: str) -> AttachSession:
        """Read-only upstream stream; never reattach/take over to keep warmth."""
        binary = self._tool()
        target = self.shell_attach_target(terminal_id)

        def factory() -> AttachSession:
            session = ObserveSession(
                [
                    binary,
                    "terminal",
                    "session",
                    "observe",
                    target,
                    "--cols",
                    "120",
                    "--rows",
                    "40",
                ],
                capture_stderr=True,
                lifecycle=self._attach_io,
            )
            session.spawn()
            return session

        self._wake_output_readers()
        pooled = self._observer_pool.acquire(
            f"{kind}:{terminal_id}", factory, self._now_ms(), allow_eviction=False
        )
        assert isinstance(pooled, AttachSession)  # noqa: S101
        self._attached[f"{kind}:{terminal_id}"] = (pooled, terminal_id, "")
        self._observe_started[terminal_id] = self._observe_now_ms()
        retry = self._observe_retries.get(terminal_id)
        if retry is not None:
            retry.deadline_ms = None
        self._output_changed.set()
        return pooled

    def attach_shell(
        self, terminal_id: str, *, cols: int = 80, rows: int = 24, takeover: bool = False
    ) -> AttachSession:
        """Acquire (pooled, retained) shell attach, WITHOUT takeover unless the gateway's
        route carries the viewer's explicit takeover. `terminal_id` is the gateway id; the
        pool stays keyed by it."""
        binary = self._tool()
        herdr_terminal = self.shell_attach_target(terminal_id)

        def factory() -> AttachSession:
            argv = plan_shell_attach(
                binary,
                herdr_terminal,
                cols=cols,
                rows=rows,
                controller=self._controller_viewport,
                takeover=takeover,
            )
            session = self._control_session(argv, cols, rows)
            session.spawn()
            return session

        self._wake_output_readers()
        pooled = self._pool.acquire(f"shell:{terminal_id}", factory, self._now_ms())
        assert isinstance(pooled, AttachSession)  # noqa: S101
        self._attached[f"shell:{terminal_id}"] = (pooled, terminal_id, "")
        self._note_geometry(terminal_id, pooled)
        self._output_changed.set()
        return pooled

    def _control_session(self, argv: list[str], cols: int, rows: int) -> AttachSession:
        kind = ControlSession if self._controller_viewport else AttachSession
        return kind(argv, cols=cols, rows=rows, capture_stderr=True, lifecycle=self._attach_io)

    def _note_geometry(self, terminal_id: str, session: AttachSession) -> None:
        """Only a controller viewport is re-asserted; a raw client's size never is."""
        if isinstance(session, ControlSession):
            self._geometry[terminal_id] = (session.cols, session.rows)

    def agent_attach_target(self, terminal_id: str) -> str:
        """Map the gateway's agent terminal ID to Herdr's required pane ID using current
        inventory. Pass through the supplied ID when the agent is not listed.
        """
        for entry in self._agents:
            if isinstance(entry, dict) and entry.get("terminal_id") == terminal_id:
                pane = entry.get("pane_id")
                if isinstance(pane, str) and pane:
                    return pane
        return terminal_id

    def attach_agent(self, target: str, *, cols: int = 80, rows: int = 24) -> AttachSession:
        """Acquire (pooled, retained) agent attach WITH explicit takeover.
        `target` is the gateway terminal id; the pool stays keyed by it."""
        binary = self._tool()
        herdr_target = self.agent_attach_target(target)

        def factory() -> AttachSession:
            argv = plan_agent_attach(
                binary,
                herdr_target,
                takeover=True,
                cols=cols,
                rows=rows,
                controller=self._controller_viewport,
            )
            session = self._control_session(argv, cols, rows)
            session.spawn()
            return session

        self._wake_output_readers()
        pooled = self._pool.acquire(f"agent:{target}", factory, self._now_ms())
        assert isinstance(pooled, AttachSession)  # noqa: S101
        self._attached[f"agent:{target}"] = (pooled, target, "")
        self._note_geometry(target, pooled)
        self._output_changed.set()
        return pooled

    def pump_attached(self, epoch_for: Callable[[str], str], *, timeout: float = 0.0) -> int:
        """Non-blocking collect from attached PTY sessions into the relay. `epoch_for` maps
        terminal ids to the live gateway ring epoch (epoch routing is link state; the source
        never guesses).
        """
        collected = 0
        for key, (session, terminal_id, _) in list(self._attached.items()):
            if getattr(session, "spawn_pending", False):
                continue
            if isinstance(session, AttachSession):
                session.collect_diagnostics()
            epoch = epoch_for(terminal_id)
            if not epoch:
                continue
            drained = bytearray()
            try:
                # Drain short PTY reads to EAGAIN within a fair per-terminal byte budget.
                drained.extend(session.read(TUNNEL_MAX_OUTPUT, timeout=timeout))
                while drained and len(drained) < TUNNEL_MAX_OUTPUT:
                    chunk = session.read_available(TUNNEL_MAX_OUTPUT - len(drained))
                    if not chunk:
                        break
                    drained.extend(chunk)
            except Exception:  # noqa: S110 - a failed probe or read never breaks the stream
                pass  # Preserve bytes already drained before a late read failure.
            if drained:
                self._stage(terminal_id, epoch, bytes(drained))
                collected += len(drained)
                if self._epoch_modes.get(terminal_id) == "observe":
                    self._observe_stable(terminal_id)
                elif isinstance(session, ControlSession):
                    self.reassert_geometry(terminal_id)
                continue
            if not session.running:
                category = self._reap_dead(key, terminal_id, session.exit_status)
                if category == "terminal_owned" and self._epoch_modes.get(terminal_id) == "control":
                    # Owned elsewhere: a typed refusal, not a reset that would respawn and loop.
                    self._attach_refused[terminal_id] = epoch
                else:
                    self._terminal_resets[terminal_id] = epoch
        return collected

    def drain_attach_refusals(self) -> dict[str, str]:
        """Terminals whose control attach Herdr refused since the last drain, by epoch."""
        refused = {t: e for t, e in self._attach_refused.items() if self._epochs.get(t) == e}
        return refused

    def _drain_dead(self, session: AttachSession, terminal_id: str, epoch: str) -> None:
        """Best-effort final drain of a dead session into the relay
        (exact bytes, gateway-provided epoch — never guessed)."""
        for _ in range(64):
            try:
                chunk = session.read(timeout=0.0)
            except Exception:  # noqa: BLE001 - dead session yields nothing
                return
            if not chunk:
                return
            self._stage(terminal_id, epoch, chunk)

    def _reap_dead(self, key: str, terminal_id: str, exit_status: int | None) -> str:
        """Drop a dead attach session loudly; the gateway epoch is kept. Returns the fixed
        diagnostic category of the death (`terminal_owned` for an upstream refusal).
        """
        held = self._attached.get(key)
        session = held[0] if held is not None else None
        category = (
            session.collect_diagnostics() if isinstance(session, AttachSession) else "unavailable"
        )
        status = exit_status if type(exit_status) is int else None
        mode = self._epoch_modes.get(terminal_id, "control")
        explicit = key.startswith("agent:") and mode == "control"
        if mode == "observe":
            self._schedule_observe_retry(terminal_id, "process_exit")
        warn = mode != "observe" or self._observe_issue(
            terminal_id, "process_exit", status, category
        )
        if warn:
            LOG.warning(
                "attach session died, dropping (terminal=%s exit_code=%r signal=%r "
                "stderr_category=%s mode=%s recovery=%s)",
                terminal_id,
                status if status is not None and status >= 0 else None,
                -status if status is not None and status < 0 else None,
                category,
                mode,
                "explicit_takeover"
                if explicit
                else ("timed_observe_retry" if mode == "observe" else "renote_epoch"),
            )
        if explicit and terminal_id in self._epochs:
            # Agent attach takes ownership: never respawn it under the same
            # epoch (that silently re-takes a pane another client took over).
            self._agent_lost[terminal_id] = self._epochs[terminal_id]
        self._wake_output_readers()
        try:
            self._pool.close(key)
            self._observer_pool.close(key)
        finally:
            self._attached.pop(key, None)
        if mode == "observe":
            self._wake_capacity_retries()
        return category

    async def close(self) -> None:
        await self._metadata_reads.close()
        if self._subscription is not None:
            await self._subscription.aclose()
            self._subscription = None
        await self._drop_pane_subscription()
        self._wake_output_readers()
        self._pool.close_all()
        self._observer_pool.close_all()
        self._attached.clear()
        if self._attach_io is not None:
            await self._attach_io.close()
            self._attach_io = None
        for timer in self._resize_timers.values():
            timer.cancel()
        self._resize_timers.clear()
        self._resize_pending.clear()
        for timer in self._correction_timers.values():
            timer.cancel()
        self._correction_timers.clear()
        self._observe_retries.clear()
        self._observe_started.clear()
        self._observe_issues.clear()
