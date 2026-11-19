# Client ownership and recovery

The application root owns page, navigation, account and terminal-session lifetimes. Its plain hook
functions run unconditionally at fixed positions. Builders return controls without inserting new
component boundaries, so keys, ancestry and retained native terminal identity remain stable.
The expanding overlay stack stays mounted even when empty; replacing it with a bare surface would
remount its children. Its scrim sits below the operations bar. The web-push panel in Settings
cancels its asynchronous work when it unmounts: leaving Settings cancels a notifications Turn off or
choice save that is still in flight, the server may or may not have received it, and the panel loads
the server's state again the next time it opens.

## Identity before recovery

Resume refreshes identity, restores the opaque route, queries pending command outcomes, fetches a
snapshot, reconnects at its cursor, and only then requests control. A failed prerequisite stops later
steps. Individual unreadable command records remain unknown without blocking the rest of recovery;
none is resubmitted. The snapshot must supply a nonempty cursor. Successful foreground return can
still be read-only when explicit takeover is required.

Routes contain destination and opaque selection keys, never credentials or titles. Unknown routes
fall back to the default. Changes navigation uses opaque sections and numbered browser-history
entries for each screen level, while reader position stays in memory. List opens push history;
dock switches retain the prior return target, and reload reopens the URL's selected terminal.

Notification taps are hints, never authorization. After validating this device's identity and host
grant, the application fetches current state and resolves the opaque reference. Logged-out or
revoked identities cannot use a stale tap to recover content, and notification text cannot execute
commands. Logout clears local credentials even if the remote revocation fails; the notice then
explains that the server session can remain until its expiry.

The sign-in callback compares its state value in constant time. A missing or mismatched state ends
in an error without an exchange and without touching stored tokens.

## Storage and process boundaries

Credentials, PKCE verifiers, pairing verifiers and composer drafts belong in secure storage.
Drafts can contain commands, paths, output and pasted secrets, so a secure-storage outage retains
them in memory and reports a capability failure rather than using plaintext. Ordinary preferences
hold presentation choices and non-sensitive identifiers, never credentials or draft text; the
[storage page](../development/storage.md) lists the secure keys and what each store holds. Platform
preferences have their own explicit, non-sensitive schemas.

Draft snapshots contain the draft string and local token/resolution markers. Import validates the
whole shape before replacing state; restore applies only to the route being restored. Process
death drops in-memory writers, queues, cursors and snapshots. Unacknowledged input becomes unknown
and is never automatically resent. Configuration changes such as rotation retain the process and its
holders rather than pretending a new authenticated session was established.

## Retained terminals

A retained registry holds at most five terminals per host. Admission touches an existing entry or
detaches the least-recently-used hidden entry. Visible holds protect entries from eviction, including
idle expiry; a full pool with every slot held refuses admission. Registry eviction detaches locally
without closing the remote process.

Leaving a detail fences the writer, releases its lease and stops the local stream while preserving
the attachment. Background pause fences immediately before awaiting stream shutdown and best-effort
lease release; server expiry covers a failed release. Disposal stops renewal, unmounts and detaches,
and the disposed object cannot be reused. Ordinary leave can retain in-memory text until its
owner is collected; revoke/logout disposal uses clear_sensitive to wipe cached output, drafts,
selection snapshots and notices explicitly. Explicit remote Close is a separate one-shot command that
needs no live attachment and is never retried automatically.

Each mounted view owns one renderer binding. Rebinding retires saved callback tokens and input
sessions before attaching the current output sink and renewing the lease. Renewal results apply
only while the captured driver, controller and lease are still current and the session is active.
Lease release precedes a view-only warm watch, preserving native output state without retaining
control authority.

A return to the foreground requests a resume unless input is already allowed. A network-link change
on the native app fences writers and pauses, then runs the resume sequence again when the app is in
the foreground and online. Snapshot/cursor recovery cancels the parked frame pull instead of waiting
for another frame.

## Continuity and presentation

Ordered terminal output never pads, reorders or silently skips an offset gap or epoch change.
Ticket-only replay before the preserved cursor is discarded as already rendered, while an empty
frame at the current cursor can still prove stream liveness. A fresh emulator fences old callbacks
and cannot inherit its predecessor's byte cursor. Missing retained history requires explicit viewer
reset and recovery at the offered base, without acquiring control or resizing as a repair.

Connection status follows terminal-stream evidence; metadata traffic alone cannot make it live.
Automatic recovery runs only for the visible eligible entry and is serialized per entry. Exited
terminals refuse recovery without network traffic. Reattach does not silently take over another
owner: conflicts disclose the owner for an explicit decision. Same-device lease reacquisition still
passes the server's current ownership checks.

Visible-size state combines layout height and keyboard inset once. Geometry measurements belong to
the terminal area and current font, avoiding a guessed attachment size followed by a disruptive
resize. Opening geometry is published through one bounded owner; equality with the previous size
does not prove the current layout has settled. Phone keyboard holds preserve grid rows while the
page shrinks, and split layouts subtract only keyboard overlap not already removed by the page.

Diagnostics emit fixed sites, categories, booleans and numbers. Exception messages, chained text,
tracebacks, drafts, output, paths and user labels do not belong in callback failure logs. Framework
change events take their authoritative value from the payload because native property patches can
arrive at a different indexed control. Coroutine-function adapters await asynchronous callbacks;
synchronous extension listeners schedule work through the content-free failure boundary.

Visible-size tracking delays a zero-inset shrink until its grace period or a confirming media event,
then coalesces keyboard/accessory measurements within COALESCE_SECONDS and COALESCE_MAX_SECONDS.
The first measurement publishes immediately; a hide/show within SWAP_SECONDS suppresses the
transient zero-inset height. Foreground activation renews its retry budget at most once per
ACTIVATION_INTERVAL, preventing a focus burst from becoming a reconnect storm.

When available, logout sends the known push device_ref to revoke that installation subscription;
unknown or foreign references are server-side no-ops. Real secure draft keys use draft-<digest>
because the adapter accepts only letters, digits, underscores and hyphens in keys. Secure-storage
restore failures preserve the in-memory draft, and corrupt payloads never partially replace it.
Every native secure-storage operation fails explicitly when the platform is unavailable.

Stream recovery mints the replacement ticket before closing the old socket. A mint failure leaves
the old socket unclosed, while a reopening failure restores pending-resume/reattach flags so an
explicit retry remains actionable. In the live-edge branch, a lease_expired mint failure also
invalidates the writer and returns a refused outcome; keeping the old socket does not preserve
input authority after lease expiry. Current-ownership guards still run between recovery awaits.

Changes history is push-only: in-app Back changes the visible level without removing browser
entries, so URL and visible level can temporarily differ. Direction follows the numbered history
step rather than section depth. Forward or reload cannot reconstruct a discarded in-memory commit
reading from its opaque route section alone.
