# Gateway credentials, dispatch and notification boundaries

## Route-specific authentication

Gateway composition does not impose one bearer rule on every route. Protected
API operations resolve their required session, host grant and capabilities.
Health checks and authentication/pairing entry routes have their own anonymous
or proof-based boundaries. Browser mutations apply their Origin/CSRF policy;
pairing accepts an absent Origin and requires a present one to equal the public origin.
Route adapters validate
request shapes and use the shared error envelope without returning raw bodies
or validation text. Ordinary JSON bodies are bounded separately from the
explicit authenticated picture-upload budget.

Session credentials are opaque random strings with server-side state. Refresh
rotates the presented pair with no reuse window. Durable work holds the store
lock before entering its transaction; origin/account/device scope remains
required even when token-possession revocation retries an expired credential.
Local denial fences apply before slow I/O so failed storage cannot restore
writers. Retry metadata is removed only after committed revocation. Upstream
OIDC validates the configured HTTPS authorization-code/PKCE profile and RS256
identity tokens, rather than accepting client grants or provider configuration.

Terminal-socket tickets are short-lived, single-use and bound to account,
device, capabilities, allowed hosts and lease. Redemption uses the ticket's
capability snapshot; live revocation separately severs registered sockets and
invalidates leases. A replacement reader fences unused tickets from its
predecessor. Ticket values and bearer credentials must not enter diagnostics.

## Enrollment, pairing and revocation

A connector has one current opaque host token; re-enrollment rotates it and
immediately invalidates the previous token. Optional private storage preserves
host token hashes/rotation/revocation across restart, but does not restore
sessions, leases or terminal streams. The read-only registry creates nothing;
missing data is empty while unreadable data raises rather than inventing state.

Public-key-bound invitations are single use and bounded by expiry and attempts.
A code lookup does not consume the invitation: proof verification precedes the
atomic claim. Unknown, expired, claimed and burned invitations all return no
match. The optional invitation file is shared through an interprocess lock,
reload-before-mutation and atomic 0600 publication. An unreadable invitation
file cannot authorize redemption.

Local pairing windows and approvals are bounded and atomic between CLI/server
processes; restart clears them. Their file stores neither verifiers nor session
tokens. Only serving startup or an explicit owner window enables requests.
Cancelling a consumed approval burns it rather than retrying credential creation.
Paired host grants do not become account-wide device administration.

The operator CLI shares a private enrollment store with the serving process and
queues host revocations through a private spool. Malformed spool entries are
quarantined, partial temporary files ignored and requested host IDs deduplicated.
One host's revocation does not affect others. Account/host revocation captures
its authorized scope before storage can fail, fences local leases immediately,
and severs active sockets/tunnels rather than waiting for credential expiry.

## Command intent and outcome

The durable backend combines the shared command journal, loop-owned outbox and
terminal/metadata stores. New prompt revision assertions are checked before
journaling; a nonzero assertion needs the observed target and matching revision.
Absent/zero makes no assertion, and deduplicated replays keep their recorded
outcome rather than re-running freshness checks. The connector checks fresh
source generation/revision again before execution.

Enqueue and outbox drain sweep expiry. The ordered tunnel writer commits the
dispatch marker immediately before sending; a missed or expired marker skips
that send. A crash between marker and send remains unknown. Accepted undispatched
commands can recover once with the same UUID, while dispatched/acked/terminal
commands are not blindly re-enqueued. Deduplication is limited to retained
journal/tombstone state, as described in [host durability](host-runtime.md).

Connector acknowledgments/results must match the journal's host binding. Forged
execution claims for never-dispatched intent are refused; a pre-execution
rejection can settle accepted intent. Unknown outcomes remain reconciliation
work rather than terminal success/failure. Command-status events deduplicate
by command ID/status and carry routing/status metadata without prompt/result
bodies. Polling and explicit prompt-resolution reservations remain account/host
bound. A missing prompt identity can be reserved without submitting prompt bytes.

Session close tears down the local attachment and queues concrete socket closes
while its remote close still travels as journaled intent. An offline connector
can therefore converge after reconnect without keeping local writers alive.
Started durable work settles on its ordered worker lane before cancellation or
connection retirement releases ownership. Terminal-output intake never waits
behind acknowledgment/result persistence.

## Generic push hints and device identity

Notification payloads contain only the exact allowlisted generic hint keys:
notification, device/host/session/event references, category and timestamps.
They do not carry terminal text, paths, commands, secrets or approval decisions.
Opaque refs resolve only through authenticated account/device checks and current
host grants; tapping fetches current state and never submits input. Deep-link
tokens are single-purpose, not single-use. Late/duplicate generic hints can still
open current state while the account/device remains authorized.

Android registration/rotation reuses the same active install record. A token-
invalid tombstone can revive on liveness proof; a reinstall-replaced tombstone
cannot. Logout deletes the subscription and pending work; unknown or foreign
opaque refs return False without disclosing another account. A killed app cannot
send logout, so its subscription can linger until invalidation, but hints remain
content-free and cannot resolve after credential revocation.

Fanout requires a granted host, known category and nonempty session/event, with
per-device event deduplication. Category choices are checked at enqueue and
again immediately before delivery. Expired hints are swept before per-device
FIFO sends. Retry backoff must fit TTL; permanent provider failure tombstones
the token and drops pending hints. Empty-outbox backoff is wakeable, while
pending work caps the wait at its nearest scheduled attempt. Retention separately
bounds terminal rows, links, tombstones and unreferenced refs.

## Browser push, SSRF and generation fences

Browser subscriptions use a separate private SQLite outbox with WAL and FULL
synchronization. Every registration advances a monotonic device generation whose
counter survives revocation and key rotation. Messages and route tokens carry
that generation; a late provider 404/410 removes only the attempted generation,
never a replacement registration. Revocation clears queued work/tokens promptly.

The provider validates HTTPS endpoints on port 443 with no userinfo, fragment or
IP literal. Canonical IDNA/lowercase hosts must match configured exact names or
label-boundary suffixes. DNS is resolved once and every address must be public;
the connection uses a validated address while TLS still verifies canonical SNI.
Redirects are disabled, response reads are bounded and connection timeouts apply.
Endpoint paths, queries, subscription keys, JWTs and payloads are never logged.
Failure diagnostics retain only provider host, status and an allowlisted reason
word or fixed category, rather than arbitrary response text.

VAPID keys live under a 0700 private directory as 0600 files. Existing valid keys
are not overwritten on restart; exclusive creation and no-follow/ownership checks
reject unsafe paths. Key rotation stops sending, removes subscriptions and queued
work, replaces VAPID and enables sending last; the topic key stays unchanged.
Clients must resubscribe. Browser HTTP identity comes from the authenticated
session, not request data. Cookie mutations need exact Origin and session CSRF
proof, while nonambient bearer requests use their explicit credential boundary.
Push responses are no-store, and opaque tap routes recheck device and host grants.
