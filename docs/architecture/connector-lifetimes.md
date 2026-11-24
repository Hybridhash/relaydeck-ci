# Connector dispatch, streams and attachment lifetimes

## Subscription and bootstrap ownership

One bounded NDJSON request uses one Unix-socket connection. An event subscription
owns a separate held connection and its collector task until cancellation and
close settle. Bootstrap waits for subscription acknowledgment before reading
snapshot and agent inventory, buffers events arriving during those reads, then
hands the buffered prefix and live subscription to the caller. Global EOF during
bootstrap fails closed and retries the whole bootstrap. Global live EOF ends the
session; a per-pane subscription failure degrades status freshness and retries
with backoff without failing the global inventory stream.

Only evidenced upstream shapes are translated. Top-level/result schema errors
refuse a snapshot; rows missing identity are skipped, display fields are clipped,
and unmapped events are counted. Server-side sequence, cursor, protocol and host
binding are not minted by the connector. Event names normalize the first
underscore to the dotted subscription spelling. Output-matched previews never
enter the exact-byte terminal path.

## Authenticated tunnel and durable dispatch

One connector link owns the authenticated host tunnel, journal and retry loop.
It pushes a validated host snapshot and seed events on connect; snapshot
replacement and event_id deduplication make metadata re-push safe. Terminal bytes
are drained once rather than appended again on every reconnect. Large snapshots
use unique transfer IDs, bounded chunks and digest verification when the gateway
advertises intake; otherwise an oversized snapshot exits loudly rather than
reconnecting forever. Mirrored limits come from gateway/http/bounds.py.

Dispatch checks host binding and canonical-body digest before acknowledgment or
execution. Intent is journaled before touching Herdr, and the dispatch marker
commits before the first attempted byte. Matching retained UUID/digest returns
the recorded outcome; a changed digest conflicts. A crash after dispatch leaves
an unknown outcome for explicit reconciliation, never blind replay. Retention
limits are described in [host durability](host-runtime.md).

Agent creation journals the created tab/root-pane identities before starting the
agent so reconciliation can reuse partial progress. Only a definite pre-launch
agent_pane_busy refusal may retry within one intent, with a 15-second readiness
budget and authority/deadline rechecks after awaits. Other typed upstream errors
produce known failure outcomes; transport, envelope and timeout failures after
the marker remain unknown. A malformed reply never proves non-delivery. New
prompts also check the source epoch and fresh agent revision through the existing
agent-list API; this is preflight, not an atomic upstream conditional write.

Gateway kinds map only to allowlisted upstream operations: prompt, tab/agent
creation, rename and explicit pane close. Shell identity may be a tab ID, so
session close attempts pane.close and falls back to tab.close only after the
specific pane_not_found refusal. Workspaces are never closed by that path.
Command results remain distinct from raw input-frame receipts. Results are sent
before a follow-up inventory read so a read failure cannot erase a known outcome.

Transient transport/protocol failures reconnect with bounded exponential backoff,
reset after hello. Rejected handshake or unauthenticated closure stops retrying.
Deterministic bad source data exits; repeated initial-push transport failures
trip a separate circuit breaker. An upstream Herdr outage retries without burning
that gateway-push breaker. Shutdown sends bye and joins connection-owned work
before closing the journal.

## Exact-byte relay and continuity

PTY output travels only through out-of-band attach clients, never subscription
preview events. Epoch routing is supplied by the gateway; unmapped PTYs are not
read until their epoch arrives. Relay capacity counts staged and in-flight bytes
per terminal. Producers check room before reading so backpressure reaches the PTY.
Overflow drops/fences only that terminal for reset, leaving other streams alive;
frames already handed to a sender remain fenced by their explicit epoch.

Live readiness removes owned FD registrations before a process can close/reuse a
descriptor. Every wake rechecks mode, epoch and retry deadlines so one busy stream
cannot starve a failed observer. Short reads drain to EAGAIN within a fair byte
budget. A late read failure still relays bytes already drained. Producer and
ordered output-consumer tasks are separate so slow sends cannot suspend PTY
collection; reserved bytes still count while a batch is blocked.

Rebootstrap after daemon death sweeps local attach clients and subscriptions and
creates a new continuity boundary. Gateway epochs remain authoritative. Observe
clients use bounded retry; control shells can resume on re-noted routes; an ended
control agent needs a new explicit-takeover epoch. Input and uncertain writes
are never replayed after reconnect. A definite ownership refusal stays typed
rather than becoming a reset loop that seizes the pane repeatedly.

## Local process and sizing ownership

Shell attach first tries without takeover; agent control requires an explicit
takeover choice. Detaching or evicting a local attach client leaves the remote
pane alive. Explicit remote close is a separate operation. Retention protects
visible terminals, evicts idle/least-active hidden clients within the slot bound,
and awaits local teardown before releasing a slot. Admission fails when all
slots are protected or eviction is disabled.

Controller mode uses pipes with newline-delimited JSON input, resize and release;
observer frames carry base64 ANSI data and viewport dimensions. Raw attach is a
separate PTY mode. Only a controller viewport is reasserted. Resize requests
coalesce to the latest size after settling; same-size resize remains a repaint
signal. A cooldown mismatch timer rechecks authority and viewport before writing.
Controller close releases sizing authority before closing the process.

One bounded CommandWriter queues complete records and drains on the loop or its
lifecycle worker without interleaving. Deadline or partial-write failure poisons
the stream and closes the session; no more records follow an uncertain prefix.
Cancelling a waiter leaves its queued record intact and uncertain, not replayed.
Diagnostic stderr is separately drained within byte limits and reduced to fixed
categories, never mixed with exact stdout terminal bytes.

## Usage evidence episodes

Reported session identity and validated foreground-process evidence have separate
ranked episodes. A process episode advances only after re-observing the same PID,
start token and lock; closing it does not extend its last-confirmed time. Workspace
generations bump on disappearance from a complete snapshot or changed label/repo
fingerprint at reconnect, not on a matching reconnect alone. Folder observations
are digested in memory and acknowledge only after committed usage apply. Mapping
and folder evidence remain separately bounded; exhausted inference capacity does
not remove exact links.
