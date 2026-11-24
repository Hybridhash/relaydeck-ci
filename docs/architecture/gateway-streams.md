# Gateway terminal and metadata continuity

## Explicit ownership and disclosure

An upstream attachment authorizes a terminal stream and can change ownership or
sizing. Shell attachment first tries without takeover; an already-owned stream
requires deliberate takeover after same-account holder disclosure. Agent control
attachment requires explicit takeover. Cross-account holders stay undisclosed.
One attachment fans out to read-only view leases and at most one live control
lease. A lease binds principal, device, terminal, attachment generation and expiry.

Reacquiring live control by its holder renews the sixty-second lease. Another
live holder conflicts; expired control is replaced rather than resurrected.
View leases cannot write or authorize a new upstream attachment. Read-only
holder observation never attaches, issues, renews or displaces ownership. Lease
release is holder-device scoped and leaves the attachment/remote process alive;
explicit attachment close is account scoped and expires every downstream lease.

Takeover/replacement advances writer generation and rotates continuity. Stale
callbacks receive stale_revision rather than driving a successor. The socket
registry captures concrete sockets before awaits, serializes writes/close and
claims closure at most once, so a delayed cleanup cannot resolve and close a
replacement. Displaced viewers receive reset metadata before closure so clients
can distinguish recovery from an offline host.

## Expiry, reader replacement and upstream sizing

After the bounded initial-attachment grace, only a live control lease keeps a
writable upstream sizing client. Observers cannot retain the direct-attach size
lock. A reader-replacement grace preserves only its existing holder and cannot
extend the original lease, be prolonged by renewal, repeated reset or invalid
resume cursors. There is no writer while the old reader is absent; explicit
release/revocation still wins. Validated cursor resume completes without
acquiring control or resizing.

Lease/initial-grace expiry drives epoch-route changes even without a browser
socket. The reconciler wakes at the nearest absolute deadline or an explicit
publish/wake signal. It records absolute deadlines before final route reads and
revalidates without yielding before wait registration: a deadline crossing or
lease change during awaited publication must not leave a stale sleep. A later
query can add an earlier deadline but cannot erase one already due.
Lifecycle pause is not an implicit policy inside the lease store; clients use
explicit release/revocation paths. Synthetic unchecked lease construction is
reserved for test/development adapters.

## Output rings and viewer queues

Each attachment has one epoch-scoped append-only ring with absolute byte offsets.
Retention is bounded by two MiB and five minutes, evicting on publication; an idle
ring retains bytes until a later publish reaps them. A single append larger than
the ring keeps its tail. Rotation drops old bytes and resets offsets so delayed
old-epoch data cannot address the new stream.

A resume outside retained history, beyond the live edge or in the wrong epoch
returns no bytes and requires explicit reset. Ticket-only first connection replays
retained bytes, including the greeting before the viewer connected. An empty
unknown epoch at offset zero starts at the live edge through the backend's resume
contract; a nonzero unknown-epoch offset is stale. Stale batches never masquerade
as a complete screen.

Each downstream connection owns a separate one-MiB FIFO. Overflow latches the
first dropped offset and refuses later pushes until reset; slow viewers fail
independently while other queues and the ring keep flowing. Rotation discards
queued old bytes and requires reattachment. Subscription capture and the replay
boundary happen without yielding so new output queues while baseline/replay is
sent. Large queued payloads are rechunked with continuous offsets for the wire.
Every exit removes the viewer's subscription, including cancellation.

Queue changes wake live viewers rather than polling. Idle viewers recheck leases
at known expiry or a bounded fallback, and every handled input/control frame
forces a trailing lease check before another receive. Rate-limited input can be
dropped without ending output, but revocation/close after a state check forbids
another send. A control sequence duplicate within the same generation is
classified as deduplicated rather than a new input delivery. Raw input logs are
synthetic observability, not proof of delivery to the host.

## Tunnel admission and ordered lanes

Only one current connector tunnel and its retiring predecessor can retain owned
work for a host. Replacement cannot accumulate unbounded queues or cleanup tasks.
Tunnel messages have strict host binding, shape and size validation. Semantic
terminal misses can drop a stale frame without killing every host stream;
malformed or unauthorized documents fail the tunnel closed. Large snapshots are
assembled once with ordered chunks, byte/digest/time bounds and unique transfer
identity. An idle tunnel wakes on socket activity; active transfer deadlines
bound its waits.

Command replies use a FIFO persistence lane; output and metadata intake do not
wait for journal storage. Both lanes count active/partial work against shared
byte admission. Shutdown joins started work, including durable marker completion.
Unattempted drained intents can return to the outbox on cancellation, but an
uncertain attempted write is never automatically resent.

Ordinary HTTP, SSE and WebSocket admission are separate. Only body collection
has a request receive deadline; long-lived streams are not given a total lifetime
timeout. Authenticated picture PUT retains its own two-slot, ten-MiB, 120-second
budget. HTTP capacity and receive-time exhaustion use a retryable 429 envelope; a saturated WebSocket
is accepted and then closed with code 1013.
Listener frame and graceful-shutdown limits are independent of those route caps.

## Metadata and immutable reads

The gateway stamps sequence, HMAC cursor, protocol and host identity at ingestion.
Per-host event IDs deduplicate within bounded process-memory retention; list-length
changes reconcile an externally supplied log index. An older cursor replays only the retained data; trimming does not enforce a resync response. Snapshots carry the authenticated event boundary and
replace host state; clients do not derive it from the first event or parse opaque
cursor contents.

Activity timestamps are observed metadata times, not invented start times, and
bootstrap-only activity is unknown. Read marks bind account, host, epoch and
resource rather than a device and do not reorder inventory pages. Command-status
transitions can advance a snapshot boundary without retaining command history in
that snapshot; newly attached clients read command status explicitly while live
consumers receive ordered events.

Immutable usage pages validate cache inputs outside report admission and coalesce
identical work. Only misses take a bounded report slot, which stays owned until
native work actually finishes even after an HTTP waiter times out or disconnects.
Explicit as-of data can remain available during refresh/saturation, but admission
alone cannot justify an unvalidated 304. Repository reads recheck account/host
ownership after off-loop work and keep cached pages inside that access boundary.
Metric state records route templates/fixed categories and aggregate timings in
fifteen one-minute buckets, never raw request paths, queries or credentials.
