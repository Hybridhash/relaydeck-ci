# Host files, command durability and connection lifetimes

## Private files and enrollment

Private-file operations pin directory descriptors and open each path component
without following links. Ancestors must have trusted ownership and permissions;
the leaf directory is owned by the current user with mode 0700. The connector's
credential and key files are the one exception: their folder (the config folder) may be
readable by others but never writable by them, as in 0.2.5. A missing folder is made 0700, and
an existing unsafe directory is refused rather than repaired. Reads accept only a bounded,
regular, private, single-link file and use nonblocking opens so a FIFO cannot
hang the caller.

Exclusive creation opens the destination itself as a new 0600 file, writes the bytes and syncs
it; the name is visible while that write is in progress. Failed creation removes only the inode
it created. Replacement writes and syncs a separate temporary file, validates the existing private
file, then rechecks the destination inode before an atomic rename. The check and rename are separate
operations, so a swap after the check can still be overwritten. Fixed error codes expose
neither paths nor OS error messages; an uncertain durability result means publication happened
but its sync failed.

Each connector provisions a host-local Ed25519 identity. Its private seed stays
on the host in a 0600 file. Enrollment binds the invitation to an OpenSSH public
key and verifies proof of possession at redemption. The OpenSSH SHA256
fingerprint can be checked with `ssh-keygen -lf`.

## Command lifecycle

`host/persistence/command_journal.py` persists the command UUID, principal,
routing target, generation, payload and canonical digest in SQLite.
Deduplication holds within the retained record/tombstone window. The same ID
and digest returns the current record without redispatch; a changed digest
raises `JournalConflict`.

| Internal state | Meaning |
| --- | --- |
| `accepted` | Intent journaled with no dispatch marker; proven undispatched |
| `dispatched` | Write-ahead marker committed before bytes can reach the tunnel; execution may or may not have happened |
| `acked` | Connector journal accepted the UUID; execution is still unproven |
| `succeeded` / `failed` | Recorded terminal outcomes; a failed upstream response can include a refusal, so failure alone does not prove execution |
| `rejected` | Terminal refusal before execution |
| `unknown` | Dispatch occurred but no definite outcome is available; reconcile explicitly |
| `expired` | Deadline passed while still accepted; proven undispatched |

The dispatch marker is written before sending. `mark_dispatched` returns False
if the command has already moved or its deadline passed; the pump must skip
that send. An expired accepted command becomes expired, while an expired
dispatched/acked command becomes unknown. Enqueue and dispatch both enforce
TTL, including existing rows on resubmission and new rows with an explicit
past deadline. An expired resubmission never becomes accepted again.

Execution claims are accepted only from dispatched, acked or unknown states.
A rejection may also settle accepted intent because it proves non-execution.
Terminal commands cannot be resurrected. Recorded partial creation identities
let reconciliation reuse already-created resources. Missing history or agent
activity does not prove delivery. Prompt resolution can atomically reserve an
unaccepted ID with a terminal, payload-free row, conflicting with a delayed
original request.

After a crash, recovery marks dispatched/acked records unknown and returns
unexpired accepted records as redispatchable. The caller may send each listed
accepted UUID once under downstream deduplication. Unknown outcomes require an
explicit record/reconciliation query, never blind resubmission.

## Retention and wire representation

Nonterminal records retain full bodies. Terminal bodies remain for 24 hours,
then `purge()` reduces them to routing/status/timestamp/digest tombstones.
Tombstones still deduplicate matching digests and reject conflicts. They are
removed after seven days; an ID resubmitted after removal is a new command.

Raw input-frame receipts are a separate table retained for one hour. Receipt
means the connector boundary received an input sequence, not command execution
or agent progress. Duplicate frame receipts return False, first receipts return
True, and neither reads nor changes command outcomes.

The v1 command-record schema has no `acked` or `expired` statuses and no
`result_digest`. `wire_status` maps acked to dispatched and expired to rejected
with `error_code="expired"`; result digests remain journal-side.

## SQLite durability limits

The main database is pre-created as 0600. SQLite uses WAL mode and
`synchronous=FULL`; mutations commit under the shared connection lock. Every
commit restores existing WAL sidecars to 0600. Sidecars inherit the process
umask when SQLite creates them during a transaction, so a permissive umask can
expose prompt bytes before the post-commit chmod. A crash in that window leaves
those permissions until the next open or commit repairs them. These guarantees
do not claim elimination of that exposure window.

The connection permits cross-thread use behind a lock because synchronous
HTTP routes, pumps and other callers can run on different threads. A failed
receipt insert rolls back its open transaction rather than retaining a write
lock. Cancellation does not abandon an active durable operation or release its
serialization slot early.

## Logs and security events

Companion, gateway and connector logs share one configuration (`host/observability/logging.py`):
timestamps are UTC with a `Z` suffix and every line has a request-id field. A record that carries an
exception keeps only a category (`timeout`, `permission`, `io`, `invalid`, `group` or
`unexpected`); the message text, traceback and arguments are discarded before any handler formats
them (`host/observability/exceptions.py`).

Security events (`host/observability/security.py`) log a fixed event name and outcome after the
state change completes, never a credential or account. The events are pairing requested, decision and
redemption, grant issued, and session, device, account, host and host-grant revocation. The outcomes
are completed, approved, denied, noop, rejected and failed, and a repeated rejected or failed
outcome for the same event is logged at most once a second.

## Connection writers

`host/transport/queues.py` uses one ordered writer per connection. Admission
counts active items as well as queued ones. A snapshot batch is admitted as
one unit while its bytes count in full, and its messages remain contiguous.
The dispatch/deadline callback runs in the writer immediately before the wire
attempt. Send receipts report actual sends; uncertain writes are never retried.
Shutdown joins every connection-owned task rather than detaching workers.
